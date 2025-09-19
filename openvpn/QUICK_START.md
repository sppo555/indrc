# OpenVPN 快速使用指南

## 🚀 三步快速部署

### 1️⃣ 安裝 OpenVPN 服務器
```bash
sudo bash install_openvpn_server.sh
```

### 2️⃣ 創建用戶
```bash
sudo ./generate_ovpn_user.sh john
```

### 3️⃣ 下載配置文件
生成的 `john.ovpn` 文件即可用於客戶端連接

---

## 📋 常用命令

### 用戶管理
```bash
# 創建新用戶
sudo ./generate_ovpn_user.sh <用戶名>

# 強制重新創建用戶
FORCE_RECREATE=true sudo ./generate_ovpn_user.sh <用戶名>

# 撤銷用戶
sudo ./manage_openvpn_users.sh revoke <用戶名>
```

### 服務管理
```bash
# 查看狀態
docker-compose ps

# 查看日誌
docker-compose logs -f openvpn

# 重啟服務
docker-compose restart openvpn
```

### 獲取幫助
```bash
./generate_ovpn_user.sh -h
./manage_openvpn_users.sh
```

---

## 🔧 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `FORCE_RECREATE` | 強制重建現有用戶 | `false` |
| `PASSWORD` | CA 證書密碼 | `1qaz2wsx` |

### 使用範例
```bash
# 使用自定義密碼創建用戶
PASSWORD=mypassword sudo ./generate_ovpn_user.sh alice

# 強制重建並使用自定義密碼
FORCE_RECREATE=true PASSWORD=mypassword sudo ./generate_ovpn_user.sh alice
```

---

## 📱 客戶端設置

1. **下載** 生成的 `.ovpn` 文件
2. **安裝** OpenVPN 客戶端軟件
3. **導入** 配置文件
4. **連接** VPN

### 推薦客戶端
- Windows: OpenVPN GUI
- macOS: Tunnelblick
- Android: OpenVPN for Android
- iOS: OpenVPN Connect
