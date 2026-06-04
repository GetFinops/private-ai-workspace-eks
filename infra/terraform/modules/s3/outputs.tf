output "bucket_name" {
  description = "Name of the S3 artifacts bucket."
  value       = aws_s3_bucket.artifacts.bucket
}

output "bucket_arn" {
  description = "ARN of the S3 artifacts bucket."
  value       = aws_s3_bucket.artifacts.arn
}

output "bucket_region" {
  description = "AWS region containing the bucket."
  value       = aws_s3_bucket.artifacts.region
}
