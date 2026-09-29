# The two public names of an environment: the API at its load balancer, the
# portal at its CloudFront distribution. Alias records, so they follow those
# targets' addresses and cost no queries.

resource "aws_route53_record" "api" {
  zone_id = var.api_zone_id
  name    = var.api_domain_name
  type    = "A"

  alias {
    name                   = var.load_balancer_dns_name
    zone_id                = var.load_balancer_zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "app" {
  for_each = toset(["A", "AAAA"])

  zone_id = var.app_zone_id
  name    = var.app_domain_name
  type    = each.key

  alias {
    name                   = var.distribution_domain_name
    zone_id                = var.distribution_zone_id
    evaluate_target_health = false
  }
}
