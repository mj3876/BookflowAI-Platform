"""One-shot: rewrite CFN Description blocks in infra/aws/**/*.yaml as ASCII English.

Run once, then delete this file (or keep in scripts/ as utility).
"""
import os, re, sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'infra', 'aws'))

NEW_DESCS = {
    # Tier 00
    '00-foundation/acm.yaml':
        'BOOKFLOW - Tier 00 - ACM Client VPN server cert skeleton (easy-rsa generated, imported via aws acm import-certificate; ARN stored in Parameter Store)',
    '00-foundation/cloudtrail.yaml':
        'BOOKFLOW - Tier 00 - CloudTrail Management events trail (S3 destination + KMS encrypted)',
    '00-foundation/cloudwatch.yaml':
        'BOOKFLOW - Tier 00 - CloudWatch Log Groups + Alarms (retention 7 days)',
    '00-foundation/codestar-connection.yaml':
        'BOOKFLOW - Tier 00 - CodeStar Connection for GitHub OAuth (CodePipeline source). Manual activation required in AWS Console (PENDING -> AVAILABLE): deploy stack, then Console > Developer Tools > Connections > Update pending connection > install GitHub App',
    '00-foundation/ecr.yaml':
        'BOOKFLOW - Tier 00 - ECR Repositories (11 total: 7 EKS Pods + 1 EKS CronJob + 3 ECS Services)',
    '00-foundation/iam.yaml':
        'BOOKFLOW - Tier 00 Foundation - IAM (GitHub Actions OIDC Provider + execution Roles for CodePipeline, CodeBuild, Ansible CN, EKS Cluster, ECS Task; one-shot stack, never destroyed)',
    '00-foundation/kms.yaml':
        'BOOKFLOW - Tier 00 - KMS CMKs x2 (EKS envelope encryption + CloudTrail log encryption)',
    '00-foundation/parameter-store.yaml':
        'BOOKFLOW - Tier 00 - SSM Parameter Store (Standard tier baseline parameters)',
    '00-foundation/secrets.yaml':
        'BOOKFLOW - Tier 00 - Secrets Manager (skeleton; populated in Phase 2 by Azure Function and Ansible)',

    # Tier 10 Network Core
    '10-network-core/customer-gateway.yaml':
        'BOOKFLOW - Tier 10 - Customer Gateway x2 (Azure VNet GW + GCP HA VPN). IP set via Parameter (placeholder until Phase 2 Azure/GCP deploy provides real public IP)',
    '10-network-core/route53.yaml':
        'BOOKFLOW - Tier 10 - Route 53 Private Hosted Zone vpn.bookflow.internal (Client VPN endpoint + service discovery; associated with all 5 VPCs: BookFlow AI, Sales Data, Egress, Data, Ansible)',
    '10-network-core/vpc-ansible.yaml':
        'BOOKFLOW - Tier 10 - Ansible VPC (10.4.0.0/16) - Public subnet (Ansible Node, outbound) + Private subnet x2',
    '10-network-core/vpc-data.yaml':
        'BOOKFLOW - Tier 10 - Data VPC (10.3.0.0/16) - RDS + Redis isolated; Private + DB subnet',
    '10-network-core/vpc-egress.yaml':
        'BOOKFLOW - Tier 10 - Egress VPC (10.2.0.0/16) - DMZ - Public subnets only + IGW',
    '10-network-core/vpc-sales-data.yaml':
        'BOOKFLOW - Tier 10 - Sales Data VPC (10.1.0.0/16) - POS simulators + ECS - Private only',

    # Tier 10 Endpoints
    '10-network-core/endpoints/endpoints-ansible.yaml':
        'BOOKFLOW - Tier 10 - Ansible VPC Interface Endpoints: SSM + SSMMessages + EC2Messages (Session Manager, GHA -> SSM SendCommand) + Secrets Manager (RDS creds) + Glue (script deploy) + S3 Gateway (Glue scripts bucket sync)',
    '10-network-core/endpoints/endpoints-bookflow-ai.yaml':
        'BOOKFLOW - Tier 10 - BookFlow AI VPC Interface Endpoints (7): ECR api, ECR dkr, Kinesis, SSM, Secrets Manager, CloudWatch Logs, KMS + S3 Gateway Endpoint (single AZ AZ1 for cost optimization)',
    '10-network-core/endpoints/endpoints-sales-data.yaml':
        'BOOKFLOW - Tier 10 - Sales Data VPC Interface Endpoints (3): ECR api/dkr (ECS Fargate image pull) + Kinesis (POS put_records) + S3 Gateway Endpoint (single AZ for cost optimization)',

    # Tier 10 Peering
    '10-network-core/peering/ansible-data.yaml':
        'BOOKFLOW - Tier 10 - VPC Peering: Ansible <-> Data. Ansible CN -> RDS (schema/seed/grants SQL apply for RDS GitOps)',
    '10-network-core/peering/bookflow-ai-data.yaml':
        'BOOKFLOW - Tier 10 - VPC Peering: BookFlow AI <-> Data. EKS Pods -> RDS/Redis access (Phase 1-2 cheap peering; replaced by TGW in Phase 3-4)',
    '10-network-core/peering/bookflow-ai-egress.yaml':
        'BOOKFLOW - Tier 10 - VPC Peering: BookFlow AI <-> Egress. auth-pod -> NAT (Azure Entra OIDC outbound) + Pods -> External ALB (publisher API ingress)',
    '10-network-core/peering/egress-data.yaml':
        'BOOKFLOW - Tier 10 - VPC Peering: Egress <-> Data. inventory API ECS (Egress) -> RDS (Data) read access',
    '10-network-core/peering/sales-data-egress.yaml':
        'BOOKFLOW - Tier 10 - VPC Peering: Sales Data <-> Egress. ECS simulators (Sales Data) -> inventory API External ALB (Egress) test path',

    # Tier 20
    '20-data-persistent/kinesis.yaml':
        'BOOKFLOW - Tier 20 - Kinesis pos-events Data Stream (5 shards) + Firehose to S3 Raw (via VPC endpoint)',
    '20-data-persistent/rds.yaml':
        'BOOKFLOW - Tier 20 - RDS PostgreSQL (Data VPC DB subnet; Single-AZ for dev, Multi-AZ for prod)',
    '20-data-persistent/redis.yaml':
        'BOOKFLOW - Tier 20 - ElastiCache Redis (Data VPC DB subnet; Single-node for dev, Replication Group for prod)',

    # Tier 30
    '30-compute-cluster/ansible-node.yaml':
        'BOOKFLOW - Tier 30 - Ansible Control Node (Ubuntu 24, t3.nano, Ansible VPC Public, SG ingress blocked, SSM Session Manager only, K8s admin entry)',
    '30-compute-cluster/ecs-cluster.yaml':
        'BOOKFLOW - Tier 30 - ECS Cluster + Fargate Capacity Provider + Container Insights',
    '30-compute-cluster/eks-alb-controller-irsa.yaml':
        'BOOKFLOW - Tier 30 - ALB Controller IRSA Role (allows AWS Load Balancer Controller Pod to apply K8s Ingress yaml via CI/CD)',
    '30-compute-cluster/eks-cluster.yaml':
        'BOOKFLOW - Tier 30 - EKS Control Plane (BookFlow AI VPC Private) + Access Entry + OIDC + KMS envelope encryption',
    '30-compute-cluster/eks-eso-irsa.yaml':
        'BOOKFLOW - Tier 30 - External Secrets Operator IRSA Role (auth-pod and other Pods sync from Secrets Manager to K8s Secret)',

    # Tier 40
    '40-compute-runtime/eks-addons.yaml':
        'BOOKFLOW - Tier 40 - EKS Core Addons (vpc-cni, kube-proxy, coredns, ebs-csi-driver, pod-identity-agent)',
    '40-compute-runtime/eks-nodegroup.yaml':
        'BOOKFLOW - Tier 40 - EKS Managed Node Group (EC2 t3.medium x2 ON_DEMAND, BookFlow AI VPC Private)',

    # Tier 50
    '50-network-traffic/alb-external.yaml':
        'BOOKFLOW - Tier 50 - External ALB (Egress VPC Public) + Target Groups (Publisher Blue/Green + inventory-api) + Listeners (HTTP 80 PROD + 8080 TEST)',
    '50-network-traffic/nat-gateway.yaml':
        'BOOKFLOW - Tier 50 - NAT Gateway x2 (Egress VPC Public, Multi-AZ HA, TGW-attached for cross-VPC use in Phase 3-4)',
    '50-network-traffic/waf.yaml':
        'BOOKFLOW - Tier 50 - WAFv2 WebACL (Regional) + External ALB Association + AWS Managed Rules + Rate Limit',

    # Tier 60
    '60-network-cross-cloud/client-vpn.yaml':
        'BOOKFLOW - Tier 60 - Client VPN Endpoint (3 user access, BookFlow AI VPC subnet, ACM mutual auth)',
    '60-network-cross-cloud/tgw-vpc-routes.yaml':
        'BOOKFLOW - Tier 60 - VPC Route Table entries pointing to TGW (cross-VPC + cross-cloud)',
    '60-network-cross-cloud/tgw.yaml':
        'BOOKFLOW - Tier 60 - Transit Gateway Hub + 4 VPC Attachments + Route Tables (Phase 3-4 cross-cloud; Ansible VPC excluded)',
    '60-network-cross-cloud/vpn-site-to-site.yaml':
        'BOOKFLOW - Tier 60 - Site-to-Site VPN (Azure + GCP) - TGW Attachment + IPSec Tunnels',

    # Tier 99
    '99-glue/glue-catalog.yaml':
        'BOOKFLOW - Tier 99-glue - Glue Database + 6 Jobs (Flex) + IAM + Connection (BigQuery) + Data Quality',
    '99-glue/step-functions.yaml':
        'BOOKFLOW - Tier 99-glue - ETL3 Step Functions State Machine (orchestrates 6 Glue Jobs)',
    '99-serverless/sam-template.yaml':
        'BOOKFLOW - Tier 99-serverless - 7 Lambdas + EventBridge cron 5 + Kinesis ESM + API Gateway HTTP',
}


def replace_description(content: str, new_desc: str) -> str:
    # Match either single-line `Description: text\n` or multiline `Description: >\n  ...`
    # Stop at next top-level YAML key (line starting with capital letter + colon, no indent).
    pattern = re.compile(
        r'^Description:.*?(?=\n[A-Z][A-Za-z0-9]*\s*:)',
        re.MULTILINE | re.DOTALL,
    )
    new_block = f'Description: {new_desc}'
    new_content, n = pattern.subn(new_block, content, count=1)
    if n == 0:
        return None  # no change
    return new_content


def main():
    changed = []
    skipped_no_match = []
    skipped_no_mapping = []
    for rel, new_desc in NEW_DESCS.items():
        fp = os.path.join(ROOT, rel.replace('/', os.sep))
        if not os.path.exists(fp):
            print(f'MISSING: {fp}')
            continue
        with open(fp, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = replace_description(content, new_desc)
        if new_content is None:
            skipped_no_match.append(rel)
            continue
        if new_content == content:
            continue
        with open(fp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_content)
        changed.append(rel)
    print(f'\nChanged: {len(changed)}')
    for r in changed:
        print(f'  {r}')
    if skipped_no_match:
        print(f'\nNo match (manual fix needed): {len(skipped_no_match)}')
        for r in skipped_no_match:
            print(f'  {r}')


if __name__ == '__main__':
    main()
