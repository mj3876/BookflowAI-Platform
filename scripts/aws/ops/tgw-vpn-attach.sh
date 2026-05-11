#!/usr/bin/env bash
# tgw-vpn-attach.sh · VPN attachment → TGW RT association + propagation (BGP 필수)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
load_env
pre_flight

step "VPN attachment → TGW RT association + propagation"

py - <<'PYEOF'
import boto3, os, sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
session = boto3.Session(profile_name=os.environ['AWS_PROFILE'], region_name=os.environ['AWS_REGION'])
ec2 = session.client('ec2')
cf  = session.client('cloudformation')

tgw_rt_id = next(
    o['OutputValue'] for o in
    cf.describe_stacks(StackName='bookflow-60-tgw')['Stacks'][0]['Outputs']
    if o['OutputKey'] == 'TgwRouteTableId'
)
print(f'TGW RT ID: {tgw_rt_id}')

atts = ec2.describe_transit_gateway_attachments(
    Filters=[{'Name': 'resource-type', 'Values': ['vpn']},
             {'Name': 'state', 'Values': ['available', 'pending']}]
)['TransitGatewayAttachments']

if not atts:
    print('VPN attachment 없음 — network-mode.sh tgw 먼저 실행')
    sys.exit(1)

for att in atts:
    att_id = att['TransitGatewayAttachmentId']
    name = next((t['Value'] for t in att.get('Tags', []) if t['Key'] == 'Name'), '?')
    print(f'\n  attachment: {att_id} ({name})')
    for fn, label in [
        (ec2.associate_transit_gateway_route_table, 'associate'),
        (ec2.enable_transit_gateway_route_table_propagation, 'propagate'),
    ]:
        try:
            fn(TransitGatewayRouteTableId=tgw_rt_id, TransitGatewayAttachmentId=att_id)
            print(f'    {label} -> OK')
        except ec2.exceptions.ClientError as e:
            print(f'    {label} -> {"already done" if "already" in str(e).lower() else str(e)[:120]}')

print('\nDone. 2~3분 후 AWS 콘솔에서 BGP STATUS: UP 확인.')
PYEOF
