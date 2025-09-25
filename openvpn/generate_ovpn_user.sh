#!/bin/bash

PASSWORD="1qaz2wsx"
OVPN_DATA="/opt/openvpn"

# 動態獲取當前外網IP
get_current_ip() {
    local ip=$(curl -s ipinfo.io/ip 2>/dev/null)
    if [[ -z "$ip" || ! "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        echo "錯誤: 無法獲取外網IP，請檢查網路連接" >&2
        exit 1
    fi
    echo "$ip"
}

add_user() {
    local username=$1
    if [[ -z "$username" ]]; then
        echo "用法: $0 add <username>"
        exit 1
    fi
    
    echo "添加用戶: $username"
    echo "獲取當前外網IP..."
    local current_ip=$(get_current_ip)
    echo "當前外網IP: $current_ip"
    
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
    
    # 生成配置文件
    docker-compose run --rm openvpn ovpn_getclient $username > $username.ovpn
    
    # 更新為當前IP地址
    # 假設原始配置使用某個默認IP，我們需要替換為當前IP
    # 查找配置文件中的 remote 行並替換IP
    if [[ -f "$username.ovpn" ]]; then
        # 使用sed替換remote行中的IP地址
        sed -i -E "s/^remote [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/remote $current_ip/" "$username.ovpn"
        echo "用戶 $username 的配置文件已生成: $username.ovpn"
        echo "服務器IP已設置為: $current_ip"
    else
        echo "錯誤: 配置文件生成失敗"
        exit 1
    fi
}

revoke_user() {
    local username=$1
    if [[ -z "$username" ]]; then
        echo "用法: $0 revoke <username>"
        exit 1
    fi
    
    echo "撤销用戶: $username"
    
    # 步骤1：撤销证书
    echo "步骤1: 撤销證書..."
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
    
    # 步骤2：生成新的 CRL (这是关键步骤！)
    echo "步骤2: 生成新的證書撤銷列表 (CRL)..."
    expect -c "
set timeout 60
spawn docker exec -it openvpn easyrsa gen-crl
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
    
    # 步骤3：复制 CRL 文件到 OpenVPN 配置目录
    echo "步骤3: 更新 OpenVPN 服务器的 CRL 文件..."
    docker exec openvpn cp /etc/openvpn/pki/crl.pem /etc/openvpn/
    
    # 步骤4：确保 OpenVPN 配置包含 crl-verify
    echo "步骤4: 檢查並更新 OpenVPN 配置..."
    docker exec openvpn sh -c "grep -q 'crl-verify' /etc/openvpn/openvpn.conf || echo 'crl-verify /etc/openvpn/crl.pem' >> /etc/openvpn/openvpn.conf"
    
    # 步骤5：重启容器以应用更改
    echo "步骤5: 重啟 OpenVPN 容器..."
    docker-compose restart openvpn
    
    # 步骤6：删除本地的 .ovpn 文件（如果存在）
    if [[ -f "$username.ovpn" ]]; then
        echo "步骤6: 刪除本地配置文件 $username.ovpn"
        rm "$username.ovpn"
    fi
    
    # 步骤7：验证撤销状态
    echo "步骤7: 等待容器啟動並驗證撤銷狀態..."
    sleep 10
    docker exec openvpn openssl crl -in /etc/openvpn/crl.pem -text -noout | grep -A 5 "Revoked Certificates" || echo "CRL 文件可能為空"
    
    echo "✅ 用戶 $username 已成功撤銷"
    echo "⚠️  注意：如果用戶仍在連接中，需要等待會話超時或手動斷開"
}

# 更新現有配置文件的IP地址為當前外網IP
update_existing_configs() {
    echo "更新現有配置文件中的IP地址..."
    echo "獲取當前外網IP..."
    local current_ip=$(get_current_ip)
    echo "當前外網IP: $current_ip"
    
    local count=0
    for ovpn_file in *.ovpn; do
        if [[ -f "$ovpn_file" ]]; then
            # 使用sed替換remote行中的IP地址
            sed -i -E "s/^remote [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/remote $current_ip/" "$ovpn_file"
            echo "已更新: $ovpn_file -> $current_ip"
            ((count++))
        fi
    done
    
    if [[ $count -eq 0 ]]; then
        echo "未找到任何 .ovpn 配置文件"
    else
        echo "✅ 共更新了 $count 個配置文件"
    fi
}

# 檢查CRL狀態和撤銷的證書
check_crl_status() {
    echo "=== CRL 狀態檢查 ==="
    
    # 檢查CRL文件是否存在
    echo "檢查 CRL 文件..."
    docker exec openvpn ls -la /etc/openvpn/crl.pem 2>/dev/null || {
        echo "❌ CRL 文件不存在！這就是為什麼撤銷不生效的原因！"
        echo "嘗試生成 CRL 文件..."
        docker exec openvpn easyrsa gen-crl
        docker exec openvpn cp /etc/openvpn/pki/crl.pem /etc/openvpn/
        echo "✅ CRL 文件已生成"
    }
    
    # 顯示撤銷的證書
    echo -e "\n=== 撤銷的證書列表 ==="
    docker exec openvpn openssl crl -in /etc/openvpn/crl.pem -text -noout | grep -A 10 "Revoked Certificates" || echo "目前沒有撤銷的證書"
    
    # 檢查OpenVPN服務器配置
    echo -e "\n=== OpenVPN 服務器配置檢查 ==="
    docker exec openvpn grep -E "(crl-verify|crl)" /etc/openvpn/openvpn.conf || {
        echo "❌ 配置文件中缺少 crl-verify 選項！"
        echo "添加 crl-verify 配置..."
        docker exec openvpn sh -c "echo 'crl-verify /etc/openvpn/crl.pem' >> /etc/openvpn/openvpn.conf"
        echo "✅ 已添加 crl-verify 配置，需要重啟容器"
    }
    
    # 顯示當前外網IP
    echo -e "\n=== 當前外網IP信息 ==="
    local current_ip=$(get_current_ip)
    echo "當前外網IP: $current_ip"
}

# 立即修復撤銷問題
fix_revocation() {
    echo "=== 修復撤銷功能 ==="
    
    # 1. 生成 CRL 文件
    echo "1. 生成 CRL 文件..."
    expect -c "
set timeout 60
spawn docker exec -it openvpn easyrsa gen-crl
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
    
    # 2. 複製到正確位置
    echo "2. 複製 CRL 文件..."
    docker exec openvpn cp /etc/openvpn/pki/crl.pem /etc/openvpn/
    
    # 3. 添加 crl-verify 配置
    echo "3. 更新配置文件..."
    docker exec openvpn sh -c "grep -q 'crl-verify' /etc/openvpn/openvpn.conf || echo 'crl-verify /etc/openvpn/crl.pem' >> /etc/openvpn/openvpn.conf"
    
    # 4. 重啟容器
    echo "4. 重啟 OpenVPN 容器..."
    docker-compose restart openvpn
    
    echo "✅ 撤銷功能修復完成！"
}

# 顯示當前IP信息
show_ip_info() {
    echo "=== 獲取詳細IP信息 ==="
    local ip_info=$(curl -s ipinfo.io 2>/dev/null)
    if [[ -n "$ip_info" ]]; then
        echo "$ip_info" | jq . 2>/dev/null || echo "$ip_info"
    else
        echo "無法獲取IP信息，請檢查網路連接"
    fi
}

case "$1" in
    add)
        add_user $2
        ;;
    revoke)
        revoke_user $2
        ;;
    update-ip)
        update_existing_configs
        ;;
    check-crl)
        check_crl_status
        ;;
    fix-revocation)
        fix_revocation
        ;;
    show-ip)
        show_ip_info
        ;;
    *)
        echo "用法: $0 {add|revoke|update-ip|check-crl|fix-revocation|show-ip} <username>"
        echo "  add            - 添加新用戶（自動使用當前外網IP）"
        echo "  revoke         - 撤銷用戶（完整流程）"
        echo "  update-ip      - 更新現有配置文件為當前外網IP"
        echo "  check-crl      - 檢查CRL狀態和配置"
        echo "  fix-revocation - 立即修復撤銷功能"
        echo "  show-ip        - 顯示詳細IP信息"
        exit 1
        ;;
esac