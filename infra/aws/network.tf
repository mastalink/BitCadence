# ── The utilidor ────────────────────────────────────────────────────────────
# Everything in this file is underground. A visitor to the city never sees a
# subnet, a NAT gateway, or a security group - they see the feed, the console,
# and the dashboards. Keep it that way: nothing here is a demo surface.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "city" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.city.id
}

# Public: only the ALB lives here.
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.city.id
  cidr_block              = cidrsubnet(aws_vpc.city.cidr_block, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-public-${count.index}", Tier = "public" }
}

# Private: gateway, workers, conductor, collector, database.
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.city.id
  cidr_block        = cidrsubnet(aws_vpc.city.cidr_block, 8, 10 + count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${var.name}-private-${count.index}", Tier = "private" }
}

# One NAT, deliberately. Two would be "production"; this is a demo that must
# stay cheap enough to leave running. The chaos suite is what proves recovery,
# not redundant plumbing.
resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.igw]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.city.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.city.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── Security groups ─────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Public entry to the console and API"
  vpc_id      = aws_vpc.city.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "gateway" {
  name        = "${var.name}-gateway"
  description = "Gateway task: only the ALB and in-VPC peers may reach it"
  vpc_id      = aws_vpc.city.id

  ingress {
    from_port       = 18789
    to_port         = 18789
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  ingress {
    from_port   = 18789
    to_port     = 18789
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.city.cidr_block]
    description = "workers, conductor, collector"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "workers" {
  name        = "${var.name}-workers"
  description = "Cloud workers and the conductor. Egress only."
  vpc_id      = aws_vpc.city.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  count       = var.store_backend == "postgres" ? 1 : 0
  name        = "${var.name}-db"
  description = "RDS: gateway and isolated chaos conductor only"
  vpc_id      = aws_vpc.city.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id, aws_security_group.conductor.id]
  }
}

resource "aws_security_group" "conductor" {
  name        = "${var.name}-conductor"
  description = "Dedicated chaos conductor with database probe access"
  vpc_id      = aws_vpc.city.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "efs" {
  count       = var.store_backend == "local" ? 1 : 0
  name        = "${var.name}-efs"
  description = "EFS for LocalStore: reachable only from the gateway task"
  vpc_id      = aws_vpc.city.id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id]
  }
}
