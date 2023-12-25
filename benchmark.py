import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Mapping, MutableSet, Tuple, MutableMapping

from kubernetes import config, client
from kubernetes.client import CoreV1Api, V1Pod

USED_NAMESPACE = "default"
node_pool_name_label_selector = "nodepoolname"  # keep this in sync with the Terraform code


def sanitize_name_rfc_1123(name: str) -> str:
    """
    Sanitizes a name to be compliant with RFC 1123 (which is the Kubernetes standard).
    See also https://kubernetes.io/docs/concepts/overview/working-with-objects/names/
    """
    sanitized_string = re.sub('[^A-Za-z0-9.-]', '', name)

    # Ensure that the name starts with a letter
    if not sanitized_string[0].isalpha():
        sanitized_string = "a" + sanitized_string

    length_limit = 253
    if len(sanitized_string) > length_limit:
        sanitized_string = sanitized_string[:length_limit]

    return sanitized_string


def get_node_pool_names() -> list[str]:
    k8s_provider = os.getenv("K8S_PROVIDER", None)
    with open(f"terraform/{k8s_provider}/vars.auto.tfvars.json") as f:
        terraform_vars = json.load(f)
        return list(terraform_vars["node_pools"].keys())


get_pod_name_from_node_pool_name = lambda node_pool_name: sanitize_name_rfc_1123(f"benchmark-{node_pool_name}")


def create_benchmark_pods(core_v1: CoreV1Api) -> list[V1Pod]:
    """
    Creates one Pod for each node pool defined in the terraform/$K8S_PROVIDER/va.rsauto.tfvars.json file, using the
    image defined in environment variable BENCHMARK_IMAGE (see README.md).
    """
    pods = []

    image = os.getenv("BENCHMARK_IMAGE", "ghcr.io/mshekow/pts-docker-benchmark:2023.12.15")

    for node_pool_name in get_node_pool_names():
        pod_manifest = {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {
                'name': get_pod_name_from_node_pool_name(node_pool_name),
                'labels': {
                    'app': 'benchmark'
                }
            },
            'spec': {
                'containers': [{
                    'image': image,
                    'name': 'benchmark'
                }],
                'restartPolicy': 'Never',
                'nodeSelector': {
                    node_pool_name_label_selector: node_pool_name
                }
            }
        }
        try:
            pod = core_v1.create_namespaced_pod(body=pod_manifest, namespace=USED_NAMESPACE)
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logging.info(f"Replacing Pod {pod_manifest['metadata']['name']} because it already exists")
                core_v1.delete_namespaced_pod(pod_manifest['metadata']['name'], USED_NAMESPACE)
                pod = core_v1.create_namespaced_pod(body=pod_manifest, namespace=USED_NAMESPACE)
            else:
                raise e
        pods.append(pod)
    return pods


def find_benchmark_pods(core_v1: CoreV1Api) -> list[V1Pod]:
    """
    Finds the Pods that were created by the create_benchmark_pods() method.
    """
    pods = []
    for node_pool_name in get_node_pool_names():
        pod_name = get_pod_name_from_node_pool_name(node_pool_name)
        pod = core_v1.read_namespaced_pod(pod_name, USED_NAMESPACE)
        pods.append(pod)
    return pods


RETRY_INTERVAL_SECONDS = 30
MAX_TIMEOUT_SECONDS = 90 * 60


def wait_for_pods_to_finish(core_v1: CoreV1Api, pods: list[V1Pod]):
    """
    Waits until all provided pods have reached the "Succeeded" or "Failed" phase.
    If at least one Pod has reached the "Failed" phase, this method raises an exception.
    """
    for _ in range(MAX_TIMEOUT_SECONDS // RETRY_INTERVAL_SECONDS):
        pod_statuses = []
        for pod in pods:
            pod_status = core_v1.read_namespaced_pod_status(pod.metadata.name, pod.metadata.namespace).status.phase
            pod_statuses.append(pod_status)

        finished_pods = [s in ["Succeeded", "Failed"] for s in pod_statuses]
        if all(finished_pods):
            if any([s == "Failed" for s in pod_statuses]):
                # Get the logs of the first failed pod
                first_failed_pod = pods[pod_statuses.index("Failed")]
                failed_pod_log = core_v1.read_namespaced_pod_log(first_failed_pod.metadata.name,
                                                                 first_failed_pod.metadata.namespace)
                raise Exception(f"Pod {first_failed_pod.metadata.name} failed, logs:\n{failed_pod_log}")
            break
        else:
            logging.info(f"Still waiting for Pods to complete: # of completed pods={sum(finished_pods)}, "
                         f"# of still-running pods={len(pod_statuses) - sum(finished_pods)}")

        time.sleep(RETRY_INTERVAL_SECONDS)

    logging.info("All benchmark pods have successfully completed")


@dataclass
class BenchmarkResult:
    vm_type: str
    tool_name: str
    tool_config: str
    result_unit: str
    result_value: str


MARKER_LINE = "benchmarkresult"


def extract_relevant_log_lines(pod_log: str, node_pool_name: str) -> list[str]:
    """
    Returns the last few lines of the provided Pod log that actually contain benchmark results.
    """
    log_lines = pod_log.splitlines(keepends=False)

    # Find the line that contains the MARKER_LINE
    marker_line_index = None
    for i, line in enumerate(log_lines):
        if line == MARKER_LINE:
            marker_line_index = i
            break
    if marker_line_index is None:
        raise ValueError(f"Unable to find the marker line '{MARKER_LINE}' in the '{node_pool_name}' Pod log")

    if "testing" not in log_lines[marker_line_index + 1]:
        raise ValueError(f"Expected 'testing' line following the marker line in the '{node_pool_name}' Pod log, "
                         f"but it is missing")

    if log_lines[marker_line_index + 2] != "":
        raise ValueError(f"Expected empty line following the 'testing' line in the '{node_pool_name}' Pod log, "
                         f"but it '{log_lines[marker_line_index + 2]}'")

    # Find the next empty line that follows marker_line_index+2
    empty_line_index = None
    for i in range(marker_line_index + 3, len(log_lines)):
        if log_lines[i] == "":
            empty_line_index = i
            break
    if empty_line_index is None:
        raise ValueError(f"Unable to find the next empty line after the marker line '{MARKER_LINE}' in "
                         f"the '{node_pool_name}' Pod log")

    if "Virtual Disk" not in log_lines[empty_line_index + 1]:
        raise ValueError(f"Expected 'Virtual Disk' line in the '{node_pool_name}' Pod log, but it is missing")

    relevant_log_lines = log_lines[empty_line_index + 2:]
    return [line for line in relevant_log_lines if line != ""]


PTS_CSV_REGEX = re.compile(
    r'^\"(?P<testtool>.*) - (?P<testconfig>.*?)\((?P<resultunit>.*)\)\",(?P<hib>.*),(?P<resultvalue>.*)$')


def extract_benchmark_results_from_pod_log(pod_log: str, node_pool_name: str) -> list[BenchmarkResult]:
    """
    Extracts the benchmark results from the provided Pod log (which contains the benchmark results in CSV format
    towards the end, so this function finds the last few relevant lines).
    """
    # Here is an example of a raw Pod log:
    """
    <lots of log lines of Phoronix test suite>
    <empty line>
    benchmarkresult  << this is the marker we look for which indicates the start of the benchmark results
    Intel Xeon Platinum 8370C testing with a Microsoft Virtual Machine (Hyper-V UEFI v4.1 BIOS) and hyperv_fb on Ubuntu 22.04.3 LTS via the Phoronix Test Suite.
    <empty line>
     ,,"Virtual Disk - Intel Xeon Platinum 8370C"
    Processor,,Intel Xeon Platinum 8370C @ 2.80GHz (2 Cores / 4 Threads)
    Motherboard,,Microsoft Virtual Machine (Hyper-V UEFI v4.1 BIOS)
    Memory,,16GB
    Disk,,137GB Virtual Disk + 24GB Virtual Disk
    Graphics,,hyperv_fb
    OS,,Ubuntu 22.04.3 LTS
    Kernel,,5.15.0-1052-azure (x86_64)
    Compiler,,GCC 11.4.0
    File-System,,overlayfs
    Screen Resolution,,1024x768
    <empty line>
     ,,"Virtual Disk - Intel Xeon Platinum 8370C"
    "Flexible IO Tester - Disk Random Write, Block Size: 4KB (MB/s)",HIB,55.2
    "Flexible IO Tester - Disk Random Write, Block Size: 4KB (IOPS)",HIB,14133
    "Flexible IO Tester - Disk Random Write, Block Size: 32KB (MB/s)",HIB,393
    "Flexible IO Tester - Disk Random Write, Block Size: 32KB (IOPS)",HIB,12567
    "Flexible IO Tester - Disk Random Write, Block Size: 256KB (MB/s)",HIB,392
    "Flexible IO Tester - Disk Random Write, Block Size: 256KB (IOPS)",HIB,1568
    "Flexible IO Tester - Disk Sequential Read, Block Size: 4MB (MB/s)",HIB,395
    "Flexible IO Tester - Disk Sequential Read, Block Size: 4MB (IOPS)",HIB,97
    "Flexible IO Tester - Disk Sequential Write, Block Size: 4MB (MB/s)",HIB,395
    "Flexible IO Tester - Disk Sequential Write, Block Size: 4MB (IOPS)",HIB,97
    "7-Zip Compression - Test: Compression Rating (MIPS)",HIB,19082
    "7-Zip Compression - Test: Decompression Rating (MIPS)",HIB,11447
    "OpenSSL - Multi core, Bytes: 1024 (byte/s)",HIB,1146236980
    "OpenSSL - Single core, Bytes: 1024 (byte/s)",HIB,531228400
    "Sysbench - Customized Sysbench CPU multi core (Events/sec)",HIB,6262.12
    "Sysbench - Disk Random Write, Block Size: 4KB (IOPS (write))",HIB,16988.68
    "Sysbench - Disk Random Write, Block Size: 4KB (MiB/s (write))",HIB,66.36
    "Sysbench - Customized Sysbench CPU single core (Events/sec)",HIB,3000.30
    "Sysbench - Disk Random Write, Block Size: 32KB (IOPS (write))",HIB,12462.88
    "Sysbench - Disk Random Write, Block Size: 32KB (MiB/s (write))",HIB,389.47
    "Sysbench - Disk Random Write, Block Size: 256KB (IOPS (write))",HIB,1570.71
    "Sysbench - Disk Random Write, Block Size: 256KB (MiB/s (write))",HIB,392.68
    "Sysbench - Disk Sequential Read, Block Size: 4MB (IOPS (read))",HIB,98.72
    "Sysbench - Disk Sequential Read, Block Size: 4MB (MiB/s (read))",HIB,394.88
    "Sysbench - Disk Sequential Write, Block Size: 4MB (IOPS (write))",HIB,98.57
    "Sysbench - Disk Sequential Write, Block Size: 4MB (MiB/s (write))",HIB,394.29
    """

    benchmark_results = []
    relevant_lines = extract_relevant_log_lines(pod_log, node_pool_name)
    for line in relevant_lines:
        # Example for line: "Flexible IO Tester - Disk Random Write, Block Size: 4KB (MB/s)",HIB,55.2
        # Extract the relevant data using a regex
        match = PTS_CSV_REGEX.search(line)
        if not match:
            raise ValueError(f"Unable to parse line '{line}' in the '{node_pool_name}' Pod log (regex failed)")

        test_tool = match.group("testtool")
        test_config = match.group("testconfig").strip()
        result_unit = match.group("resultunit")
        hib = match.group("hib")
        result_value = match.group("resultvalue")

        if hib != "HIB":
            raise ValueError(f"Invalid value '{hib}' in line '{line}' in "
                             f"the '{node_pool_name}' Pod log (expected 'HIB')")

        benchmark_results.append(BenchmarkResult(vm_type=node_pool_name, tool_name=test_tool, tool_config=test_config,
                                                 result_unit=result_unit, result_value=result_value))

    return benchmark_results


ENV_VAR_NORMALIZED_RESULTS = "ADD_NORMALIZED_RESULTS"


def collect_benchmark_results(pod_logs: Mapping[str, str]) -> str:
    """
    Given the pod logs, this function returns a string that contains the parsed benchmark results in CSV format
    (using semicolon as separator). It groups the results so that the resulting CSV file has the following structure:
    <tool name + config + result unit>;<vm type 1>;<vm type 2>;...
    If ENV_VAR_NORMALIZED_RESULTS is set to "true", it also adds one normalized column per vm type, where the smallest
    value is set to 100 and the other values are expressed as percent.
    """
    # Parse benchmark results
    benchmark_results: MutableMapping[str, list[BenchmarkResult]] = {}  # maps from the node_pool_name to the results
    for node_pool_name, pod_log in pod_logs.items():
        benchmark_results[node_pool_name] = extract_benchmark_results_from_pod_log(pod_log, node_pool_name)

    # Get unique listing of all tool names and tool configs
    tool_names_with_config_and_unit: MutableSet[Tuple[str, str, str]] = set()
    for node_pool_name, benchmark_result_items in benchmark_results.items():
        for benchmark_result_item in benchmark_result_items:
            tool_names_with_config_and_unit.add(
                (benchmark_result_item.tool_name, benchmark_result_item.tool_config, benchmark_result_item.result_unit))

    tool_names_with_config_sorted: list[Tuple[str, str, str]] = list(tool_names_with_config_and_unit)
    tool_names_with_config_sorted.sort()

    header_line = "Tool name + config + result unit"
    for node_pool_name in get_node_pool_names():
        header_line += f";{node_pool_name}"
    if os.getenv(ENV_VAR_NORMALIZED_RESULTS) == "true":
        for node_pool_name in get_node_pool_names():
            header_line += f";{node_pool_name} (normalized)"

    benchmark_result_lines = [header_line]

    for tool_name, tool_config, result_unit in tool_names_with_config_sorted:
        benchmark_result_line = f"{tool_name} - {tool_config} ({result_unit})"
        normalized_results = []
        for node_pool_name in get_node_pool_names():
            benchmark_result_items = benchmark_results[node_pool_name]
            found_matching_result = False
            for benchmark_result_item in benchmark_result_items:
                if benchmark_result_item.tool_name == tool_name and benchmark_result_item.tool_config == tool_config and benchmark_result_item.result_unit == result_unit:
                    normalized_results.append(float(benchmark_result_item.result_value))
                    benchmark_result_line += f";{benchmark_result_item.result_value}"
                    found_matching_result = True
                    break
            if not found_matching_result:
                raise ValueError(f"Unable to find benchmark result for tool '{tool_name}' and config '{tool_config}' "
                                 f"for node pool '{node_pool_name}'")

        if os.getenv(ENV_VAR_NORMALIZED_RESULTS) == "true":
            min_value = min(normalized_results)
            normalized_results = [round(100 * x / min_value, 2) for x in normalized_results]
            for normalized_result in normalized_results:
                benchmark_result_line += f";{normalized_result}"

        benchmark_result_lines.append(benchmark_result_line)

    return "\n".join(benchmark_result_lines)


def get_pod_logs(core_v1: CoreV1Api) -> Mapping[str, str]:
    """
    Returns a mapping from the node pool name to pod log.
    """
    pod_logs = {}
    for node_pool_name in get_node_pool_names():
        pod_name = get_pod_name_from_node_pool_name(node_pool_name)
        pod_log = core_v1.read_namespaced_pod_log(pod_name, USED_NAMESPACE)
        pod_logs[node_pool_name] = pod_log
    return pod_logs


def store_raw_logs(core_v1: CoreV1Api, pods: list[V1Pod]):
    """
    Stores the raw logs of the benchmark Pods in files named "raw-results/<YYYY-MM-DD-HH-MM>/<node_pool_name>.log".
    """
    os.makedirs("raw-results", exist_ok=True)
    result_folder_name = f"raw-results/{time.strftime('%Y-%m-%d-%H-%M')}"
    os.makedirs(result_folder_name, exist_ok=True)

    for pod in pods:
        pod_log = core_v1.read_namespaced_pod_log(pod.metadata.name, pod.metadata.namespace)
        node_pool_name = pod.spec.node_selector[node_pool_name_label_selector]
        with open(f"{result_folder_name}/{node_pool_name}.log", "w") as f:
            f.write(pod_log)


def get_local_pod_logs() -> Mapping[str, str]:
    """
    Returns a mapping from the node pool name to the pod log.
    """
    pod_logs = {}
    existing_log_folder_name = should_reparse_existing_raw_logs()
    for node_pool_name in get_node_pool_names():
        with open(f"raw-results/{existing_log_folder_name}/{node_pool_name}.log", "r") as f:
            pod_log = f.read()
        pod_logs[node_pool_name] = pod_log
    return pod_logs


def should_reparse_existing_raw_logs() -> str:
    return os.getenv("REPARSE_EXISTING_RAW_LOGS", "")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if should_reparse_existing_raw_logs():
        pod_logs = get_local_pod_logs()
    else:
        config.load_kube_config(config_file="kubeconfig")
        core_v1 = client.CoreV1Api()
        # Verify that the credentials are working (raises otherwise)
        core_v1.list_node()

        if os.getenv("SKIP_POD_CREATION") == "true":
            pods = find_benchmark_pods(core_v1)
        else:
            pods = create_benchmark_pods(core_v1)
        wait_for_pods_to_finish(core_v1, pods)
        pod_logs = get_pod_logs(core_v1)

    csv_content = collect_benchmark_results(pod_logs)
    with open("benchmark_results.csv", "w") as f:
        f.write(csv_content)

    if not should_reparse_existing_raw_logs():
        store_raw_logs(core_v1, pods)
