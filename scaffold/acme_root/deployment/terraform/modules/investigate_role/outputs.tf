output "arn" {
  description = "The role_arn of this environment's investigate profile."
  value       = aws_iam_role.this.arn
}

output "name" {
  value = aws_iam_role.this.name
}
