---
name: network-tools
description: >
  网络诊断与配置技能。覆盖：连通性测试(ping/traceroute/mtr)、端口扫描(nmap/nc)、
  DNS解析、带宽测试、抓包分析(tcpdump)、防火墙配置(iptables/ufw)、
  SSL证书检查、HTTP调试(curl)、代理配置、VPN排错。
  触发关键词：网络、ping、traceroute、端口、扫描、nmap、dns、带宽、
  防火墙、iptables、ufw、ssl、证书、curl、代理、vpn、tcpdump、抓包、
  连不上、超时、拒绝连接、网络不通。
  遇到任何网络问题排查必须使用本技能。

## Keywords
网络 ping traceroute mtr 端口 扫描 nmap nc dns dig nslookup
带宽 bandwidth iperf 防火墙 iptables ufw firewall ssl 证书 tls
curl wget 代理 proxy vpn tcpdump 抓包 wireshark
连不上 超时 timeout refused 拒绝连接 网络不通 丢包
---

# Network Tools Skill

## 连通性诊断

### 基础三步
```bash
# 1. Ping（ICMP）
ping -c 4 <target>

# 2. TCP 端口连通
nc -zv <host> <port> -w 3
# 或
curl -s --connect-timeout 3 telnet://<host>:<port>

# 3. 路由追踪
traceroute <target>
# 或更好的
mtr -r -c 10 <target>
```

### DNS
```bash
dig <domain> +short
dig <domain> @8.8.8.8          # 指定DNS服务器
nslookup <domain>
host <domain>
dig -x <ip>                    # 反向解析
```

### HTTP 调试
```bash
# 详细请求
curl -v https://example.com 2>&1

# 只看响应头
curl -I https://example.com

# 计时分析
curl -w "\nDNS: %{time_namelookup}s\nConnect: %{time_connect}s\nTTFB: %{time_starttransfer}s\nTotal: %{time_total}s\n" -o /dev/null -s https://example.com

# POST JSON
curl -X POST -H "Content-Type: application/json" -d '{"key":"val"}' http://localhost:8080/api

# 带认证
curl -H "Authorization: Bearer <token>" http://localhost:8080/api
```

### 端口扫描
```bash
# 快速扫描常用端口
nmap -F <target>

# 扫描指定范围
nmap -p 1-10000 <target>

# 检查本机监听
ss -tlnp
netstat -tlnp 2>/dev/null
lsof -i -P -n | grep LISTEN
```

### 防火墙
```bash
# UFW
ufw status verbose
ufw allow 8080/tcp
ufw deny from <ip>

# iptables
iptables -L -n -v
iptables -A INPUT -p tcp --dport 8080 -j ACCEPT

# firewalld
firewall-cmd --list-all
firewall-cmd --add-port=8080/tcp --permanent
firewall-cmd --reload
```

### SSL/TLS
```bash
# 检查证书
openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -dates -subject

# 证书到期时间
echo | openssl s_client -connect <host>:443 2>/dev/null | openssl x509 -noout -enddate
```

### 抓包
```bash
# 抓指定端口
tcpdump -i any port 8080 -c 50 -nn

# 抓指定IP
tcpdump -i any host <ip> -c 50 -nn

# 保存pcap文件
tcpdump -i any port 443 -w capture.pcap -c 1000
```
