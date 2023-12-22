variable "resource_group_location" {
  type        = string
  default     = "westeurope"
  description = "Location of the resource group."
}

variable "username" {
  type        = string
  description = "The admin username for the new cluster."
  default     = "azureadmin"
}

variable "node_pools" {
  type = map(object({
    vm_size         = string
    os_disk_type    = optional(string)
    os_disk_size_gb = optional(number)
  }))
  default     = {}
  description = "Worker node pools"
}

variable "kubernetes_version" {
  type    = string
  default = "1.28.3"
}
