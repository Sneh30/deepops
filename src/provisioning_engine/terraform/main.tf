terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  tags = merge(
    {
      "managed-by" = "opspilot-cloud"
    },
    var.tags,
  )

  metrics_cidrs = length(var.metrics_scrape_cidrs) > 0 ? var.metrics_scrape_cidrs : ["127.0.0.1/32"]
}

resource "azurerm_resource_group" "host" {
  name     = "rg-${var.host_name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "host" {
  name                = "vnet-${var.host_name}"
  address_space       = [var.vnet_address_space]
  location            = azurerm_resource_group.host.location
  resource_group_name = azurerm_resource_group.host.name
  tags                = local.tags
}

resource "azurerm_subnet" "host" {
  name                 = "snet-${var.host_name}"
  resource_group_name  = azurerm_resource_group.host.name
  virtual_network_name = azurerm_virtual_network.host.name
  address_prefixes     = [var.subnet_prefix]
}

# Baseline inbound policy: default-deny, then explicit allows only for the
# declared application ports and the metrics scrape path. RDP is denied unless
# the declarative host config explicitly enables it (ENGINEERING_REFERENCE §5.2).
resource "azurerm_network_security_group" "host" {
  name                = "nsg-${var.host_name}"
  location            = azurerm_resource_group.host.location
  resource_group_name = azurerm_resource_group.host.name
  tags                = local.tags

  dynamic "security_rule" {
    for_each = { for index, port in var.allowed_inbound_tcp_ports : tostring(port) => index }
    content {
      name                       = "allow-tcp-${security_rule.key}"
      priority                   = 1000 + security_rule.value
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = security_rule.key
      source_address_prefix      = "*"
      destination_address_prefix = "*"
    }
  }

  security_rule {
    name                       = "allow-metrics-9182"
    priority                   = 2000
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "9182"
    source_address_prefixes    = local.metrics_cidrs
    destination_address_prefix = "*"
  }

  dynamic "security_rule" {
    for_each = var.rdp_enabled ? [1] : []
    content {
      name                       = "allow-rdp-explicit"
      priority                   = 3000
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = "3389"
      source_address_prefixes    = var.rdp_source_cidrs
      destination_address_prefix = "*"
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "host" {
  subnet_id                 = azurerm_subnet.host.id
  network_security_group_id = azurerm_network_security_group.host.id
}

resource "azurerm_public_ip" "host" {
  name                = "pip-${var.host_name}"
  location            = azurerm_resource_group.host.location
  resource_group_name = azurerm_resource_group.host.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_network_interface" "host" {
  name                = "nic-${var.host_name}"
  location            = azurerm_resource_group.host.location
  resource_group_name = azurerm_resource_group.host.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.host.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.host.id
  }
}

resource "random_password" "admin" {
  length           = 24
  special          = true
  override_special = "!#%*-_=+"
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
}

resource "azurerm_windows_virtual_machine" "host" {
  name                  = var.host_name
  computer_name         = substr(var.host_name, 0, 15)
  resource_group_name   = azurerm_resource_group.host.name
  location              = azurerm_resource_group.host.location
  size                  = var.vm_size
  admin_username        = var.admin_username
  admin_password        = random_password.admin.result
  network_interface_ids = [azurerm_network_interface.host.id]
  tags                  = local.tags

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = var.windows_image_sku
    version   = "latest"
  }

  provision_vm_agent = true
  timezone           = var.timezone

  identity {
    type = "SystemAssigned"
  }
}

# Application firewall rules and Windows features/roles are applied by the
# OpsPilot.Baseline PowerShell module after Terraform provisions the host.
# Keeping OS-level hardening on the host (not in a cloud NSG) is deliberate:
# the same baseline must hold for any provider and for on-premises hosts.
