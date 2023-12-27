resource "azurerm_resource_group" "rg" {
  location = var.resource_group_location
  name     = "aks-performance-benchmark"
}


resource "random_pet" "azurerm_kubernetes_cluster_dns_prefix" {
  prefix = "dns"
}

resource "azurerm_kubernetes_cluster" "k8s" {
  location            = azurerm_resource_group.rg.location
  name                = "benchmark-aks"
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = "benchmarkaks1"
  kubernetes_version  = var.kubernetes_version

  identity {
    type = "SystemAssigned"
  }

  # Node pool for control plane nodes
  default_node_pool {
    name                 = "systempool"
    vm_size              = "Standard_D2_v2"
    node_count           = 1
    enable_auto_scaling  = false
    orchestrator_version = var.kubernetes_version
    upgrade_settings { # set the platform-side default also on the client-side to avoid unnecessary updates
      max_surge = "10%"
    }
  }

  linux_profile {
    admin_username = var.username

    ssh_key {
      key_data = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDezBD2cY3b9TnxMz96GJPQiK0tAnNf71GZTVUMwiynKJXYH2nz/rz6WX4PxO4KGP84uvJTUKOkjcGW7QkbAcCtqDzTHwqvnYqarE09fB1FhkN9K1iYw1UqOKBjMWA8eiMBUq+wf0xNa8SXFQGH+qhSfLg3QzBCjmouS2fbGYYMd18Lt+1tsVkfdUpxVOntN5JghkYTMvScAMU0h0JOLLGfMf0d1dwwhA3hNcQ5veeiJKIcPHOLc2SZZoPL4woovxg3lTUxl/ctcsoO2OlQ442oHSZrBSC/yHFVeNQzN+88OEAG9RV/weFXGCqYDuJxKQcgQkcPE5ZE9syuWn72V7l+y7wlu4EGxdWyCPvLvL6xGJrmq/rRldXU+kbrcD26C75mdieRvERHlzxfzRvMx/3x9sHBwM+nFHUwUdfdPzuutK/bVndpBqyWoJm4xfmvQy204kcVbipkBvj/YiTi7DlCV03w2ac927IRatsVrReomlf2qYJMNGQCEFKvS+M2qTYKiUa7966/SRVO2gXAi5sSc3qA34cCpjUonvdHsjvu9uecCGcBhR8kuWMB2sVqBFldZgkJ4g/RoqO1ZxDIAoK2auHy88nx3ykE5Xoa55agPzHHKGrt3lQwf+MT9yGm8lJTKWpSbpTNXzJG4kGD9uMqFYfPmmeFc9bMUG5PzIw3Yw== mariusshekow@MacBook-Pro-von-Marius.local"
    }
  }
  network_profile {
    network_plugin    = "kubenet"
    load_balancer_sku = "standard"
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "workload_node_pools" {
  for_each = var.node_pools

  name                  = each.key
  kubernetes_cluster_id = azurerm_kubernetes_cluster.k8s.id
  mode                  = "User"
  vm_size               = each.value.vm_size
  node_count            = 1
  enable_auto_scaling   = false
  orchestrator_version  = var.kubernetes_version

  node_labels = {
    "nodepoolname" = each.key
  }

  os_disk_type    = each.value.os_disk_type
  os_disk_size_gb = each.value.os_disk_size_gb
}
