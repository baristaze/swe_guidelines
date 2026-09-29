output "arn" {
  description = "The certificate, available only once validated."
  value       = aws_acm_certificate_validation.this.certificate_arn
}
