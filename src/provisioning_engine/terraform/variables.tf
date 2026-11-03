variable "host_name" {
  description = "Windows host name from the declarative host configuration."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]{0,62}$", var.host_name))
    error_message = "host_name must satisfy the host-config schema."
  }
}

variable "location" {
  description = "Cloud region for the reference Azure implementation."
  type        = string
  default     = "eastus"
}

variable "vm_size" {
  description = "Azure VM size for the single-host MVP."
  type        = string
  default     = "Standard_B2s"
}

variable "windows_image_sku" {
  description = "Windows Server image SKU. Windows Server 2019+ is the supported floor."
  type        = string
  default     = "2022-datacenter-g2"
}

variable "admin_username" {
  description = "Local administrator name for the provisioned host."
  type        = string
  default     = "opspilot-admin"
}

variable "timezone" {
  description = "Windows timezone name declared by the host configuration."
  type        = string
  default     = "UTC"
}

variable "vnet_address_space" {
  description = "Address space for the managed host virtual network."
  type        = string
  default     = "10.20.0.0/16"
}

variable "subnet_prefix" {
  description = "Subnet prefix for the managed host."
  type        = string
  default     = "10.20.1.0/24"
}

variable "allowed_inbound_tcp_ports" {
  description = "Explicit inbound TCP allow-list; everything else stays denied."
  type        = list(number)
  default     = [80, 443]

  validation {
    condition     = alltrue([for port in var.allowed_inbound_tcp_ports : port >= 1 && port <= 65535])
    error_message = "Every allowed port must be between 1 and 65535."
  }
}

variable "metrics_scrape_cidrs" {
  description = "Source CIDRs allowed to scrape windows_exporter on TCP 9182."
  type        = list(string)
  default     = ["127.0.0.1/32"]

  validation {
    condition     = alltrue([for cidr in var.metrics_scrape_cidrs : can(cidrhost(cidr, 0))])
    error_message = "metrics_scrape_cidrs must contain valid CIDR blocks."
  }
}

variable "rdp_enabled" {
  description = "Whether RDP is explicitly enabled. Defaults to false per the hardening baseline."
  type        = bool
  default     = false
}

variable "rdp_source_cidrs" {
  description = "Source CIDRs allowed to reach RDP when rdp_enabled is true."
  type        = list(string)
  default     = ["10.20.0.0/16"]
}

variable "tags" {
  description = "Non-secret tags applied to every provisioned resource."
  type        = map(string)
  default     = {}
}
