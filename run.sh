#!/bin/bash

set -e

# Abort if environment variable K8S_PROVIDER is not set
if [ -z "$K8S_PROVIDER" ]; then
  echo "You must set the K8S_PROVIDER environment variable, e.g. to 'AKS'"
  exit 1
fi

max_parallel=50

source venv/bin/activate

cd terraform/$K8S_PROVIDER
terraform init
terraform apply -auto-approve -parallelism=$max_parallel
terraform output -raw kube_config > ../../kubeconfig
cd ../..

echo "Giving nodes a minute to be completely ready..."
sleep 60

echo "Running benchmark..."
python3 benchmark.py

echo "Destroying cluster..."
cd terraform/$K8S_PROVIDER
terraform destroy -auto-approve -parallelism=$max_parallel
