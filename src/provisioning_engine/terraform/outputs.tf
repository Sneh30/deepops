output "resource_group_name" {
  description = "Resource group containing the managed host."
  value       = azurerm_resource_group.host.name
}

output "host_id" {
  description = "Azure resource ID of the Windows virtual machine."
  value       = azurerm_windows_virtual_machine.host.id
}

output "private_ip" {
  description = "Private address used by the control plane for management traffic."
  value       = azurerm_network_interface.host.private_ip_address
}

output "public_ip" {
  description = "Public address reserved for the reverse proxy."
  value       = azurerm_public_ip.host.ip_address
}

output "network_security_group_id" {
  description = "Baseline NSG applied to the host subnet."
  value       = azurerm_network_security_group.host.id
}

output "admin_username" {
  description = "Local administrator name (password is generated and never written to config)."
  value       = var.admin_username
}
