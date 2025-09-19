#!/bin/bash

# OpenVPN 用戶OVPN文件生成腳本
# 用法: $0 <username>
# 環境變數:
#   FORCE_RECREATE=true  - 強制重新創建現有用戶（不提示）
#   PASSWORD=<password>  - 設定自定義密碼（預設: 1qaz2wsx）

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

# 顯示使用說明
show_usage() {
    echo -e "${BLUE}OpenVPN 用戶 OVPN 文件生成腳本${NC}"
    echo -e "用法: $0 <username>"
    echo
    echo -e "環境變數:"
    echo -e "  ${YELLOW}FORCE_RECREATE=true${NC}  - 強制重新創建現有用戶（不提示）"
    echo -e "  ${YELLOW}PASSWORD=<password>${NC}   - 設定自定義密碼（預設: 1qaz2wsx）"
    echo
    echo -e "範例:"
    echo -e "  $0 john"
    echo -e "  FORCE_RECREATE=true $0 john"
    echo -e "  PASSWORD=mypassword $0 john"
    exit 1
}

# 檢查參數（先處理幫助請求）
case "$1" in
    -h|--help|help)
        show_usage
        ;;
    "")
        error "請提供用戶名參數。使用 $0 -h 查看說明"
        ;;
esac

# 檢查是否為 root 用戶
if [[ $EUID -ne 0 ]]; then
   error "此腳本需要 root 權限運行"
fi

# 配置變數
OVPN_DATA="/opt/openvpn"
PASSWORD="${PASSWORD:-1qaz2wsx}"  # 允許通過環境變數設定

USERNAME="$1"

# 檢查必要的依賴
check_dependencies() {
    log "檢查依賴..."
    
    # 檢查 Docker
    if ! command -v docker &> /dev/null; then
        error "Docker 未安裝，請先安裝 Docker"
    fi
    
    # 檢查 Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        error "Docker Compose 未安裝，請先安裝 Docker Compose"
    fi
    
    # 檢查 expect
    if ! command -v expect &> /dev/null; then
        error "expect 未安裝，請先安裝: apt install -y expect"
    fi
    
    # 檢查 OpenVPN 容器是否運行
    if ! docker ps | grep -q openvpn; then
        warn "OpenVPN 容器未運行，嘗試啟動..."
        if [[ -f "docker-compose.yml" ]]; then
            docker-compose up -d || error "無法啟動 OpenVPN 服務"
            sleep 10
            log "OpenVPN 服務已啟動"
        else
            error "docker-compose.yml 不存在，請先運行服務器安裝腳本"
        fi
    fi
    
    # 檢查 PKI 是否已初始化
    if [[ ! -f "$OVPN_DATA/pki/ca.crt" ]]; then
        error "PKI 未初始化，請先運行主安裝腳本"
    fi
    
    log "所有依賴檢查完成"
}

# 撤銷用戶證書（內部函數）
revoke_user_certificate() {
    local username=$1
    
    log "撤銷用戶證書: $username"
    
    expect -c "
set timeout 60
spawn docker exec openvpn easyrsa revoke $username
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
" || warn "證書撤銷可能失敗"
    
    # 重新生成 CRL（證書撤銷列表）
    docker exec openvpn easyrsa gen-crl || warn "CRL 生成失敗"
    
    # 重新啟動容器以應用撤銷列表（使用更安全的方式）
    log "重新載入 OpenVPN 配置..."
    docker exec openvpn kill -USR1 1 2>/dev/null || docker restart openvpn
    sleep 3
}

# 創建客戶端證書
create_client_certificate() {
    local username=$1
    
    log "創建客戶端證書: $username"
    
    # 檢查用戶是否已存在
    if docker exec openvpn test -f "/etc/openvpn/pki/issued/${username}.crt" 2>/dev/null; then
        warn "用戶 $username 的證書已存在"
        
        # 在非互動模式下，提供環境變數選項
        if [[ -n "$FORCE_RECREATE" && "$FORCE_RECREATE" == "true" ]]; then
            log "強制重新創建模式已啟用"
        else
            echo -n "是否要重新創建? (y/N): "
            read -r REPLY
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                log "跳過證書創建"
                return 0
            fi
        fi
        
        # 先撤銷現有證書
        log "撤銷現有證書..."
        revoke_user_certificate "$username"
    fi
    
    # 檢查 docker-compose.yml 是否存在
    if [[ ! -f "docker-compose.yml" ]]; then
        error "docker-compose.yml 不存在，請先運行服務器安裝腳本"
    fi
    
    # 使用 expect 自動輸入密碼創建證書
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
" || error "客戶端證書創建失敗"
    
    log "客戶端證書創建成功: $username"
}

# 生成客戶端配置文件
generate_client_config() {
    local username=$1
    local output_file="${username}.ovpn"
    
    log "生成客戶端配置文件: $output_file"
    
    # 生成 OVPN 配置文件
    docker-compose run --rm openvpn ovpn_getclient "$username" > "$output_file" || error "配置文件生成失敗"
    
    # 檢查文件是否成功生成
    if [[ ! -f "$output_file" || ! -s "$output_file" ]]; then
        error "配置文件生成失敗或文件為空"
    fi
    
    log "配置文件生成成功: $output_file"
    return 0
}


# 驗證生成的配置文件
verify_config_file() {
    local username=$1
    local config_file="${username}.ovpn"
    
    log "驗證配置文件: $config_file"
    
    # 檢查文件大小
    local file_size=$(stat -f%z "$config_file" 2>/dev/null || stat -c%s "$config_file" 2>/dev/null || echo 0)
    if [[ $file_size -lt 1000 ]]; then
        warn "配置文件可能不完整，大小只有 ${file_size} bytes"
        return 1
    fi
    
    # 檢查必要內容
    local required_sections=("BEGIN CERTIFICATE" "BEGIN PRIVATE KEY" "BEGIN OpenVPN Static key")
    for section in "${required_sections[@]}"; do
        if ! grep -q "$section" "$config_file"; then
            warn "配置文件缺少必要部分: $section"
            return 1
        fi
    done
    
    log "配置文件驗證通過"
    return 0
}

# 顯示結果信息
show_result() {
    local username=$1
    local config_file="${username}.ovpn"
    
    echo
    echo -e "${BLUE}=== 用戶創建完成 ===${NC}"
    echo -e "用戶名: ${GREEN}$username${NC}"
    echo -e "配置文件: ${GREEN}$config_file${NC}"
    echo -e "文件大小: ${GREEN}$(ls -lh "$config_file" | awk '{print $5}')${NC}"
    echo
    echo -e "${BLUE}=== 使用說明 ===${NC}"
    echo -e "1. 下載配置文件: ${YELLOW}$config_file${NC}"
    echo -e "2. 導入到 OpenVPN 客戶端"
    echo -e "3. 連接到 VPN 服務器"
    echo
    echo -e "${BLUE}=== 管理命令 ===${NC}"
    echo -e "查看服務狀態: ${YELLOW}docker-compose ps${NC}"
    echo -e "查看日誌: ${YELLOW}docker-compose logs -f openvpn${NC}"
    echo -e "撤銷用戶: ${YELLOW}./manage_openvpn_users.sh revoke $username${NC}"
    echo
}

# 主執行邏輯
main() {
    log "開始為用戶 $USERNAME 生成 OVPN 配置文件..."
    
    # 1. 檢查依賴
    check_dependencies
    
    # 2. 創建客戶端證書
    create_client_certificate "$USERNAME"
    
    # 3. 生成客戶端配置文件
    generate_client_config "$USERNAME"
    
    # 4. 驗證配置文件
    if verify_config_file "$USERNAME"; then
        log "用戶 $USERNAME 的 OVPN 配置文件創建成功！"
        show_result "$USERNAME"
    else
        error "配置文件驗證失敗，請檢查日誌"
    fi
}

# 執行主函數
main
