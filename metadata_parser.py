"""
Helper script which creates the meta-data CSV data, for AKS/Azure-based benchmarks, using the Azure pricing "API" and
parsing our own result files (to determine the CPU model and family).

Note that you need to set env var RESULTS_DIR to the date that contains the result files.
"""
import json
import os
import re
import urllib.request
from pathlib import Path

from benchmark import get_node_pool_names


def getAzureVmPricing() -> dict:
    url = 'https://azure.microsoft.com/api/v3/pricing/virtual-machines/calculator/?culture=en-us&discount=mca&billingAccount=&billingProfile=&v=20231220-1500-393622'
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        json_as_string = response.read()

    return json.loads(json_as_string)


CPU_MODEL_REGEX = re.compile(r'^\s*PROCESSOR:\s*(?P<model>.*)$')
CPU_FAMILY_REGEX = re.compile(r'^\s*Core Family:\s*(?P<family>.*)$')


def get_cpu_model_and_family(node_pool_name: str) -> tuple[str, str]:
    """
    Returns the CPU model and family for the given node pool name.
    """
    # get the CPU model and family from the result file
    log = Path(f"raw-results/{os.getenv('RESULTS_DIR')}/{node_pool_name}.log").read_text()
    lines = log.splitlines()
    cpu_model = ""
    cpu_family = ""
    for line in lines:
        cpu_model_match = CPU_MODEL_REGEX.search(line)
        if cpu_model_match:
            cpu_model = cpu_model_match.group('model').strip()

        cpu_family_match = CPU_FAMILY_REGEX.search(line)
        if cpu_family_match:
            cpu_family = cpu_family_match.group('family').strip()
            break

    if not cpu_model:
        raise Exception(f"Failed to determine CPU model and family for node pool {node_pool_name}")

    return cpu_model, cpu_family


# for vm_type, data in json_object['offers'].items():
#     if 'dc8v2' in vm_type:
#         i = 2  # dc8sv2

print("VM type;CPU;vCPU count;RAM (GB);Best supp. Managed disk type;Supp. Eph (GB);Hourly VM price (West Europe)")

os.environ["K8S_PROVIDER"] = "aks"

azurePricing = getAzureVmPricing()

for node_pool_name in get_node_pool_names():
    # remove "man" or "eph" suffix
    node_pool_name_short = node_pool_name[:-3]
    # dc8v2 is missing in azurePricing (for unknown reasons) --> need to use dc8sv2 instead
    if node_pool_name_short == "dc8v2":
        node_pool_name_short = "dc8sv2"
    price = azurePricing['offers'][f'linux-{node_pool_name_short}-standard']['prices']['perhour']['europe-west'][
        'value']
    ram = azurePricing['offers'][f'linux-{node_pool_name_short}-standard']['ram']
    cpu = azurePricing['offers'][f'linux-{node_pool_name_short}-standard']['cores']

    cpu_model, cpu_family = get_cpu_model_and_family(node_pool_name)

    cpu_family = f" ({cpu_family})" if cpu_family else ""

    print(f"{node_pool_name};{cpu_model}{cpu_family};{cpu};{int(ram)};;;{price}")
