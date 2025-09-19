#!/bin/bash

# OpenVPN 服務器安裝腳本 - Debian
set -e

# 顏色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日誌函數
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# 檢查是否為 root 用戶
if [[ $EUID -ne 0 ]]; then
   error "此腳本需要 root 權限運行"
fi

# 配置變數
OVPN_DATA="/opt/openvpn"
PASSWORD="1qaz2wsx"

log "開始 OpenVPN 服務器安裝和配置..."

# 1. 更新系統並安裝必要套件
log "更新系統套件..."
apt update -y
apt upgrade -y

log "安裝必要套件..."
apt install -y curl wget gnupg lsb-release ca-certificates

# 2. 安裝 Docker
log "安裝 Docker..."
if ! command -v docker &> /dev/null; then
    # 添加 Docker 官方 GPG key
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    
    # 添加 Docker 倉庫
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # 更新套件列表並安裝 Docker
    apt update -y
    apt install -y docker-ce docker-ce-cli containerd.io
    
    # 啟動並啟用 Docker 服務
    systemctl start docker
    systemctl enable docker
    
    log "Docker 安裝完成"
else
    log "Docker 已經安裝"
fi

# 3. 安裝 Docker Compose
log "安裝 Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
    curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose
    log "Docker Compose 安裝完成"
else
    log "Docker Compose 已經安裝"
fi

# 4. 安裝 expect（用於自動化交互）
if ! command -v expect &> /dev/null; then
    log "安裝 expect..."
    apt install -y expect
fi

# 5. 關閉防火牆
log "關閉防火牆..."
systemctl stop ufw 2>/dev/null || true
systemctl disable ufw 2>/dev/null || true
systemctl stop iptables 2>/dev/null || true
systemctl disable iptables 2>/dev/null || true

# 6. 獲取外部 IP
log "獲取外部 IP 地址..."
VM_EX_IP=$(curl -s ipinfo.io/ip)
if [[ -z "$VM_EX_IP" ]]; then
    error "無法獲取外部 IP 地址"
fi
log "外部 IP: $VM_EX_IP"

# 7. 創建 OpenVPN 目錄
log "創建 OpenVPN 目錄..."
mkdir -p $OVPN_DATA

# 8. 生成 OpenVPN 配置
log "生成 OpenVPN 配置..."
docker run -v $OVPN_DATA:/etc/openvpn --rm kylemanna/openvpn ovpn_genconfig -u udp://$VM_EX_IP

# 9. 初始化 PKI（自動輸入密碼）
log "檢查 PKI 狀態..."
if [[ ! -f "$OVPN_DATA/pki/ca.crt" ]]; then
    log "初始化 PKI..."
    expect -c "
set timeout 300
spawn docker run -v $OVPN_DATA:/etc/openvpn --rm -it kylemanna/openvpn ovpn_initpki
expect {
    \"Enter New CA Key Passphrase:\" {
        send \"$PASSWORD\r\"
        exp_continue
    }
    \"Re-Enter New CA Key Passphrase:\" {
        send \"$PASSWORD\r\"
        exp_continue
    }
    \"Common Name*\" {
        send \"OpenVPN-CA\r\"
        exp_continue
    }
    \"Enter pass phrase*\" {
        send \"$PASSWORD\r\"
        exp_continue
    }
    eof {
        exit 0
    }
    timeout {
        puts \"Timeout occurred\"
        exit 1
    }
}
"

    if [[ $? -ne 0 ]]; then
        error "PKI 初始化失敗"
    fi
else
    log "PKI 已存在，跳過初始化"
fi

# 10. 創建 docker-compose.yml
if [[ ! -f "docker-compose.yml" ]]; then
    log "創建 docker-compose.yml..."
    cat > docker-compose.yml << EOF
version: '3'
services:
  openvpn:
    container_name: openvpn
    image: kylemanna/openvpn:latest
    cap_add:
      - NET_ADMIN
    ports:
      - 1194:1194/udp
    restart: unless-stopped
    volumes:
      - /opt/openvpn:/etc/openvpn
    sysctls:
      net.ipv6.conf.all.disable_ipv6: "1"
EOF
else
    log "docker-compose.yml 已存在，跳過創建"
fi

# 11. 啟動 OpenVPN 服務
if ! docker ps | grep -q openvpn; then
    log "啟動 OpenVPN 服務..."
    docker-compose up -d
    # 等待容器啟動
    sleep 10
else
    log "OpenVPN 服務已在運行"
fi

# 12. 創建用戶管理腳本
if [[ ! -f "manage_openvpn_users.sh" ]]; then
    log "創建用戶管理腳本..."
    cat > manage_openvpn_users.sh << 'EOF'
#!/bin/bash

PASSWORD="1qaz2wsx"
OVPN_DATA="/opt/openvpn"

add_user() {
    local username=$1
    if [[ -z "$username" ]]; then
        echo "用法: $0 add <username>"
        exit 1
    fi
    
    echo "添加用戶: $username"
    expect -c "
set timeout 60
spawn docker-compose run --rm openvpn easyrsa build-client-full $username nopass
expect {
    \"Enter pass phrase*\" {
        send \"$PASSWORD\r\"
        exp_continue
    }
    eof {
        exit 0
    }
    timeout {
        puts \"Timeout occurred\"
        exit 1
    }
}
"
    
    docker-compose run --rm openvpn ovpn_getclient $username > $username.ovpn
    echo "用戶 $username 的配置文件已生成: $username.ovpn"
}

revoke_user() {
    local username=$1
    if [[ -z "$username" ]]; then
        echo "用法: $0 revoke <username>"
        exit 1
    fi
    
    echo "撤銷用戶: $username"
    expect -c "
set timeout 60
spawn docker exec -it openvpn easyrsa revoke $username
expect {
    \"Type the word 'yes'*\" {
        send \"yes\r\"
        exp_continue
    }
    \"Enter pass phrase*\" {
        send \"$PASSWORD\r\"
        exp_continue
    }
    eof {
        exit 0
    }
    timeout {
        puts \"Timeout occurred\"
        exit 1
    }
}
"
    
    docker restart openvpn
    echo "用戶 $username 已被撤銷"
}

case "$1" in
    add)
        add_user $2
        ;;
    revoke)
        revoke_user $2
        ;;
    *)
        echo "用法: $0 {add|revoke} <username>"
        echo "  add    - 添加新用戶"
        echo "  revoke - 撤銷用戶"
        exit 1
        ;;
esac
EOF

    chmod +x manage_openvpn_users.sh
else
    log "用戶管理腳本已存在，跳過創建"
fi

# 13. 驗證安裝
log "驗證安裝..."
if docker ps | grep -q openvpn; then
    log "OpenVPN 服務器安裝成功！"
else
    error "OpenVPN 服務器安裝失敗"
fi

# 14. 顯示完成信息
log "OpenVPN 服務器安裝完成！"
echo
echo -e "${BLUE}=== 服務器信息 ===${NC}"
echo -e "OpenVPN 服務器地址: ${GREEN}$VM_EX_IP:1194${NC}"
echo -e "數據目錄: ${GREEN}$OVPN_DATA${NC}"
echo -e "服務狀態: ${GREEN}$(docker ps --format 'table {{.Names}}\t{{.Status}}' | grep openvpn || echo '未運行')${NC}"
echo
echo -e "${BLUE}=== 管理命令 ===${NC}"
echo -e "查看服務狀態: ${YELLOW}docker-compose ps${NC}"
echo -e "查看日誌: ${YELLOW}docker-compose logs -f openvpn${NC}"
echo -e "添加用戶: ${YELLOW}./manage_openvpn_users.sh add <username>${NC}"
echo -e "撤銷用戶: ${YELLOW}./manage_openvpn_users.sh revoke <username>${NC}"
echo -e "生成用戶配置: ${YELLOW}./generate_ovpn_user.sh <username>${NC}"
echo
echo -e "${BLUE}=== 後續步驟 ===${NC}"
echo -e "1. 使用 ${YELLOW}./generate_ovpn_user.sh <username>${NC} 創建用戶"
echo -e "2. 下載生成的 .ovpn 文件"
echo -e "3. 導入到 OpenVPN 客戶端使用"
echo
