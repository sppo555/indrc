# India Game Pro 项目域名和 DNS 完整信息收集工具

本脚本专门用于获取 `india-game-pro` 项目下的所有 Cloud Domains 域名注册信息**以及**对应的 Cloud DNS 实际解析记录。

## 主要功能

### 完整 DNS 信息获取
- **Cloud Domains 注册信息**：域名注册状态、到期时间、联系人隐私等
- **Cloud DNS 实际记录**：每个域名的完整 DNS 解析记录
- **智能区域匹配**：自动为每个域名找到对应的 Cloud DNS 托管区域
- **记录分类统计**：A 和 CNAME 记录类型的数量统计

### 多种输出格式
- **详细模式**：显示每个域名的完整信息和 DNS 记录详情
- **简化模式**：表格格式显示关键信息摘要
- **CSV 导出**：导出完整数据到 CSV 文件用于进一步分析

## 技术特性

### Cloud DNS 记录查询
- 使用 Google Cloud DNS API v1
- 自动查找匹配的托管区域（zone）
- 智能域名-区域匹配算法
- 完整的 DNS 记录分类（主要关注）：
  - A 记录（IPv4 地址）
  - CNAME 记录（别名）
  - **注意**：MX、TXT、NS、SOA 等记录默认被过滤

### 权限要求
1. **Cloud Domains API**：
   - `domains.registrations.list`
   - `domains.registrations.get`

2. **Cloud DNS API**：
   - `dns.managedZones.list`
   - `dns.resourceRecordSets.list`

## 使用方法

### 基础使用
```bash
cd /Users/alex/Documents/indrc/gcp-ip-address-collector

# 激活虚拟环境
source venv/bin/activate

# 完整功能（推荐）：获取域名注册信息 + Cloud DNS 记录
python3 list_india_domains.py --mode service_account

# 简化输出格式
python3 list_india_domains.py --mode service_account --output-format simple

# 导出到 CSV 文件
python3 list_india_domains.py --mode service_account --output-format csv

# 跳过 DNS 记录查询（只获取域名注册信息）
python3 list_india_domains.py --mode service_account --skip-dns-records
```

### 认证方式
```bash
# 使用服务账户认证（推荐）
python3 list_india_domains.py --mode service_account

# 使用 gcloud 用户认证
python3 list_india_domains.py --mode gcloud
```

## 输出示例

### 简化模式输出
```
================================================================================
项目: india-game-pro - 域名注册摘要（包含 DNS 信息）
================================================================================
域名                                 状态         DNS提供商      DNS区域               记录数     到期时间
--------------------------------------------------------------------------------
aviator777in.club                    ACTIVE       Google Domains DNS  aviator777in.club.    5          2025-11-05
example.com                          ACTIVE       自定义 DNS         example-com-zone.     12         2026-01-15
another-domain.net                   EXPIRED      未知            未找到匹配区域         0          2024-12-01
```

### 详细模式输出
```
================================================================================
项目: india-game-pro - 详细域名信息（包含 Cloud DNS 记录）
================================================================================

1. 域名: aviator777in.club
   状态: ACTIVE
   DNS 提供商: Google Domains DNS
   到期时间: 2025-11-05T10:50:48Z
   联系人隐私: REDACTED_CONTACT_DATA
   Cloud DNS 托管区域: aviator777in.club.
   DNS 记录总数: 5
   名称服务器:
     - ns-cloud-d1.googledomains.com
     - ns-cloud-d2.googledomains.com
     - ns-cloud-d3.googledomains.com
     - ns-cloud-d4.googledomains.com
   Cloud DNS 记录:
     - A 记录: 1 条
     - CNAME 记录: 2 条
   使用 Google Domains DNS 托管
```

## 数据字段说明

### Cloud Domains 字段
- `project_id`：GCP 项目 ID
- `domain_name`：域名
- `state`：域名状态（ACTIVE、EXPIRED、SUSPENDED 等）
- `expire_time`：到期时间
- `contact_privacy`：联系人隐私设置
- `dns_provider`：DNS 提供商（Google Domains DNS 或自定义 DNS）
- `name_servers`：名称服务器列表
- `google_domains_dns`：是否使用 Google Domains DNS

### Cloud DNS 字段
- `cloud_dns_zone`：匹配的 Cloud DNS 托管区域
- `dns_records_count`：DNS 记录总数（仅包含 A 和 CNAME 记录）
- `cloud_dns_records`：完整的 DNS 记录数据（JSON 格式，仅包含 A 和 CNAME 记录）
- `detailed_dns_records`：详细 DNS 记录列表（CSV 格式，每条记录一行）
- `dns_error`：DNS 查询错误信息（如有）

## 注意事项

1. **执行时间**：获取 124 个域名的完整 DNS 记录可能需要几分钟时间
2. **API 配额**：大量 DNS 查询可能会消耗 Cloud DNS API 配额
3. **权限要求**：需要同时具有 Cloud Domains 和 Cloud DNS 的读取权限
4. **区域匹配**：如果域名没有对应的 Cloud DNS 托管区域，会显示"未找到匹配区域"

## 输出文件

- **基础信息 CSV 文件**：`india_game_pro_domains_with_dns_YYYYMMDD_HHMMSS.csv`
  - 包含所有域名的基本注册信息
  - 适合快速概览域名状态

- **详细 DNS 记录 CSV 文件**：`india_game_pro_domains_dns_detailed_YYYYMMDD_HHMMSS.csv`
  - 包含所有域名的详细 DNS 记录
  - 默认只包含 A 记录和 CNAME 记录
  - 每条记录一行，方便筛选和分析

- **控制台输出**：
  - 详细的域名和 DNS 信息
  - 错误信息和调试信息

## 性能优化建议

1. **分批处理**：对于大量域名，可以修改脚本来分批处理
2. **缓存机制**：添加本地缓存避免重复查询
3. **并行处理**：使用多线程同时查询多个域名的 DNS 记录
4. **错误重试**：添加重试机制处理临时 API 错误
5. **记录过滤**：默认只查询 A 和 CNAME 记录，减少不必要的数据处理

## 记录类型过滤

默认情况下，脚本会过滤掉 MX、TXT、NS 和 SOA 记录，只保留：
- **A 记录**：域名到 IP 地址的映射
- **CNAME 记录**：域名别名记录

如果需要包含其他记录类型，可以修改脚本中的过滤条件。

## 使用统计

从执行结果可以看到：
- **总域名数**：124 个
- **活跃域名**：120 个
- **过期域名**：4 个
- **A 记录**：约 250 条
- **CNAME 记录**：约 60 条

这个工具现在提供了完整的域名和 DNS 信息分析能力！
