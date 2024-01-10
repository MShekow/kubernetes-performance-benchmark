# Kubernetes-based performance benchmark
This is a scripting framework based on Terraform, a Docker image of [Phoronix Test Suite](https://www.phoronix-test-suite.com/) and a Python script, which performs hardware benchmarks on a Kubernetes cluster.

The goal is to choose the right VM type(s) for your workload, which has the right performance and the best price. Today's cloud providers offer a lot of choice regarding different VM types, which makes it difficult to choose the right (virtual) hardware.

This framework assumes that there is a Docker image (such as [this one](https://github.com/MShekow/pts-docker-benchmark)) that contains all your benchmark tools and whose `ENTRYPOINT/CMD` eventually prints the benchmark report to the console. This framework then uses Kubernetes to deploy the benchmarks to many different kinds of nodes, collects the result, and turns them into a _single_ CSV file.


## How it works

The `run.sh` script:

- Creates a Kubernetes cluster using Terraform, with one node pool for each VM type you want to benchmark. This repo contains an example for Azure Kubernetes Service (AKS), but it can be easily extended to support other providers, such as AWS Elastic Kubernetes Service (EKS)
- Deploys one `Pod` per VM type in the just-created Kubernetes cluster, pinning it to the right VM type via a `nodeSelector`. This `Pod` has only one container, running a customized Phoronix Test Suite Docker image (see [here](https://github.com/MShekow/pts-docker-benchmark), it runs CPU & disk performance benchmarks) 
- Waits for all `Pod`s to complete, then collects and parses the logs, producing a CSV file named `benchmark_results.csv` with the combined results. The CSV file has the following structure:
  ```
  Tool name + config + result unit;<vm type 1>;<vm type 2>;...
  7-Zip Compression - Test: Compression Rating (MIPS);<vm type 1 result>;<vm type 2 result>;...
  7-Zip Compression - Test: Decompression Rating (MIPS);<vm type 1 result>;<vm type 2 result>;...
  Flexible IO Tester - Disk Random Write, Block Size: 256KB (IOPS);<vm type 1 result>;<vm type 2 result>;...
  Flexible IO Tester - Disk Random Write, Block Size: 256KB (MB/s);<vm type 1 result>;<vm type 2 result>;...
  ...<more results>...
  ```
  
  Note that the _raw_ results are also stored in the `raw-logs/<YYYY-MM-DD-HH-mm>` directory, in case you want to re-parse them later (see below)
- Destroy the Kubernetes cluster via Terraform

This framework can be easily extended to run other benchmarks (e.g. to benchmark GPU capabilities), by using a different Docker image.

See [this blog post](https://www.augmentedmind.de/?p=3313) for background information and how to visualize the benchmark results.

## Prerequisites
- Terraform CLI must be installed, to spin up and tear down the Kubernetes cluster. Note that only an **Azure** example is available
- A Python interpreter must be installed, version 3.10 or higher
- Create a Python virtual environment: `python3 -m venv venv` and activate it: `source venv/bin/activate`
- Install dependencies: `pip install -r requirements.txt`

## Customizing the benchmark

_Before_ you run the benchmark, you need to change a few settings:

- In `terraform/<provider>/vars.auto.tfvars.json`, configure the VM types you want to benchmark
  - Note: for **AKS**, the `os_disk_type` can be set to `Ephemeral` or `Managed`. `Managed` is the default. If you choose `Ephemeral`, make sure that the chosen VM type does have _Temp storage_, and that the value of `os_disk_size_gb` does not exceed the max. available _Temp storage_ size for the chosen VM type. For instance, the [Standard_D2ds_v5](https://learn.microsoft.com/en-us/azure/virtual-machines/ddv5-ddsv5-series) VM type has a max. _Temp storage_ size of **75** GiB, so you must set `os_disk_size_gb` to 75 or lower.
- To configure a different Docker image, set the environment variable `BENCHMARK_IMAGE` to the image name, e.g. `export BENCHMARK_IMAGE=some-docker-image:v1.2.3`. In this case, you also have to adapt the `extract_benchmark_results_from_pod_log()` function.

## Running the benchmark
- Make sure your system is configured such that Terraform has the permissions to provision the K8s cluster. The specifics depend on your chosen K8s provider.
  - For instance, for `AKS`, this would mean that you either run `az login` (to use your own account), or you set the `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET` and `ARM_TENANT_ID` environment variables (to use a service principal)
- Configure the Kubernetes provider for which you want to run the experiment: `export K8S_PROVIDER=aks`
- If you want two additional columns in the resulting CSV file that contains the _normalized_ results (the smallest value being normalized to `100%`), set the environment variable `ADD_NORMALIZED_RESULTS` to "true"
- Run the experiment: `./run.sh` - this script runs Terraform to provision the K8s cluster, then runs Python which schedules the benchmark `Pod`s and scrapes the results, then calls Terraform again to tear down the infrastructure
- If any errors occurred, e.g. while running the Python script, you need to **manually** tear down the infrastructure via `terraform destroy -auto-approve`


## _Re_-parsing the benchmark results

In case the benchmark Pods have already completed, but you want to parse the logs again (e.g. because you changed the benchmark and need to adapt the parsing code), the following environment variables may be useful:
- `REPARSE_EXISTING_RAW_LOGS`: if you set this to the folder name (`<YYYY-MM-DD-HH-mm>`) of an existing `raw-logs` folder, the script will re-parse the logs directly from that folder (which works even if the cluster has already been destroyed)
- `SKIP_POD_CREATION`: if you set this to `true`, the script will not create new Pods, but expect them to already exist (in a successfully-finished state). The script then retrieves the Pod logs and parses them.

## Visualizing the benchmark results
The [Example benchmark Excel sheet](./Example%20benchmark.xlsx) demonstrates how you can visualize the results.
