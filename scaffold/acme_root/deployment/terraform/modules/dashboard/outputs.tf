output "name" {
  description = "The dashboard's name in the CloudWatch console."
  value       = aws_cloudwatch_dashboard.this.dashboard_name
}
