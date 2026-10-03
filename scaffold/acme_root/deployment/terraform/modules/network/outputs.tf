output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Where the load balancer and the NAT gateway live."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Where every task lives: the private subnets of the load balancer's zones."
  value       = slice(aws_subnet.private[*].id, 0, length(local.zones))
}

output "data_subnet_ids" {
  description = "Where the database and the cache may live: a private subnet in every zone."
  value       = aws_subnet.private[*].id
}

output "load_balancer_security_group_id" {
  value = aws_security_group.load_balancer.id
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}

output "database_security_group_id" {
  value = aws_security_group.database.id
}

output "cache_security_group_id" {
  value = aws_security_group.cache.id
}
