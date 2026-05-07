#!/bin/bash
# scripts/test-connectivity.sh
# VPN      VM   
#    VM  

set -e

RESOURCE_GROUP="rg-bookflow"
PREFIX="bookflow"
TEST_VM_NAME="vm-test-connectivity"

echo "========================================"
echo "    VM "
echo "========================================"

read -p "AWS EC2  IP (ping  ): " AWS_EC2_IP

# ──  VM  ─────────────────────────────────────────
echo ""
echo "[1]  VM  (snet-services )"
az vm create \
  --resource-group $RESOURCE_GROUP \
  --name $TEST_VM_NAME \
  --image Ubuntu2204 \
  --vnet-name "vnet-${PREFIX}" \
  --subnet snet-services \
  --size Standard_B1s \
  --admin-username azureuser \
  --generate-ssh-keys \
  --output table

#  IP 
VM_PUBLIC_IP=$(az vm show \
  --resource-group $RESOURCE_GROUP \
  --name $TEST_VM_NAME \
  --show-details \
  --query publicIps --output tsv)

echo "VM  IP: $VM_PUBLIC_IP"

# ──   ───────────────────────────────────────────
echo ""
echo "[2] AWS EC2 ping "
echo "SSH  VM    :"
echo "  ssh azureuser@$VM_PUBLIC_IP"
echo "  ping -c 4 $AWS_EC2_IP"
echo ""
echo " ping   ..."
ssh -o StrictHostKeyChecking=no \
    -o ConnectTimeout=10 \
    azureuser@$VM_PUBLIC_IP \
    "ping -c 4 $AWS_EC2_IP" || echo "ping  — Security Group/NSG  VPN   "

# ──  VM  ─────────────────────────────────────────
echo ""
echo "[3]  VM "
read -p "   VM  Enter"
az vm delete \
  --resource-group $RESOURCE_GROUP \
  --name $TEST_VM_NAME \
  --yes

# VM    (NIC,  IP, )
az network nic delete \
  --resource-group $RESOURCE_GROUP \
  --name "${TEST_VM_NAME}VMNic" 2>/dev/null || true
az network public-ip delete \
  --resource-group $RESOURCE_GROUP \
  --name "${TEST_VM_NAME}PublicIP" 2>/dev/null || true

echo " VM     "
