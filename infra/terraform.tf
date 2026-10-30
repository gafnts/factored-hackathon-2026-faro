terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.70.0"
    }
  }

  required_version = "~> 1.16.0"
  backend "s3" {
    bucket = "placeholder-tfstate-bucket"
    key    = "placeholder/service/terraform.tfstate"
  }
}
