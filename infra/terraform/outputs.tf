output "starting_stack" {
  description = "Documented first-pass AWS stack decisions for implementation."
  value       = local.starting_stack
}

output "control_plane_node_instance_types" {
  description = "Selected CPU node types for the first control-plane baseline."
  value       = var.control_plane_node_instance_types
}

output "gpu_node_instance_types" {
  description = "Selected GPU node types for the first inference-plane baseline."
  value       = var.gpu_node_instance_types
}
