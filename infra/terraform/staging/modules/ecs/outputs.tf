output "cluster_name" { value = aws_ecs_cluster.main.name }
output "cluster_arn" { value = aws_ecs_cluster.main.arn }
output "service_name" { value = aws_ecs_service.api.name }
output "ecs_sg_id" { value = aws_security_group.ecs.id }
output "task_definition_arn" { value = aws_ecs_task_definition.api.arn }
