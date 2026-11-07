<#
.SYNOPSIS
    把 PC 的以太网口接到 K1 所在的 192.168.88.0/24 网段，并验证可达性。

.DESCRIPTION
    用于「3568 第二网口 → PC」这条调试通道。分三种情形自动判定：

      1. 链路未建立（网线没插 / 对端口没通电）  → 退出码 2
      2. 已经拿到 192.168.88.x 的 DHCP 租约       → 说明两口二层互通，无需任何配置
      3. 只有 169.254 链路本地地址                → 配静态 192.168.88.50/24 再试

    为什么第 3 步值得试：即便 3568 的两口是独立网卡、不做转发，只要 PC 与 3568
    点对点之后 3568 能转发，静态地址就能通。反之如果 3568 不转发，这一步会失败，
    失败本身就是结论——说明这条通道走不通，得改用交换机或直接换线。

.PARAMETER Reset
    撤销本脚本设置的静态地址，把网口交回 DHCP（169.254 状态）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\k1-link.ps1
    powershell -ExecutionPolicy Bypass -File scripts\k1-link.ps1 -Reset
#>
param(
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$TargetIP  = '192.168.88.10'
$StaticIP  = '192.168.88.50'
$PrefixLen = 24

function Find-EthAdapter {
    # 只认物理以太网口：按描述排除虚拟网卡，避免动到 VMware 的 VMnet1/8
    Get-NetAdapter |
        Where-Object {
            $_.InterfaceDescription -notlike '*Virtual*' -and
            $_.InterfaceDescription -notlike '*VMware*' -and
            $_.InterfaceDescription -notlike '*Bluetooth*' -and
            $_.InterfaceDescription -notlike '*Wi-Fi*' -and
            $_.InterfaceDescription -notlike '*Wireless*'
        } |
        Select-Object -First 1
}

$ad = Find-EthAdapter
if (-not $ad) {
    Write-Host '[失败] 找不到物理以太网口' -ForegroundColor Red
    exit 1
}

Write-Host ("网口：{0} / {1}" -f $ad.Name, $ad.InterfaceDescription)
Write-Host ("状态：{0}  速率：{1}" -f $ad.Status, $ad.LinkSpeed)

if ($Reset) {
    Write-Host '→ 撤销静态地址，交回 DHCP'
    Remove-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetRoute     -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface  -InterfaceIndex $ad.ifIndex -Dhcp Enabled -ErrorAction SilentlyContinue
    Write-Host '   已恢复 DHCP'
    exit 0
}

if ($ad.Status -ne 'Up') {
    Write-Host '[阻塞] 链路未建立：网线没插，或对端口没通电。' -ForegroundColor Yellow
    Write-Host '       3568 第二网口插上后重跑本脚本。'
    exit 2
}

# ---- 步骤 1：看有没有现成的 DHCP 租约 ----
$ip = Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
      Where-Object { $_.IPAddress -notlike '169.254.*' }

if ($ip) {
    Write-Host ("当前地址：{0}/{1}（来源 {2}）" -f $ip.IPAddress, $ip.PrefixLength, $ip.PrefixOrigin) -ForegroundColor Green
    if ($ip.IPAddress -like '192.168.88.*') {
        Write-Host '→ 已落在 192.168.88.0/24，说明 3568 两网口二层互通，无需配置'
    } else {
        Write-Host ("→ 地址不在目标网段（{0}），建议 -Reset 后让 DHCP 重新分配" -f $ip.IPAddress) -ForegroundColor Yellow
    }
} else {
    # ---- 步骤 2：没有租约，配静态再看 ----
    Write-Host '没有 DHCP 租约（或仍是 169.254 链路本地），尝试配静态地址…'
    try {
        New-NetIPAddress -InterfaceIndex $ad.ifIndex -IPAddress $StaticIP -PrefixLength $PrefixLen -ErrorAction Stop | Out-Null
        Write-Host ("   已设 {0}/{1}" -f $StaticIP, $PrefixLen)
    } catch {
        Write-Host ("[失败] 配静态地址出错：{0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host '       多半是没有管理员权限，请用管理员终端重跑。'
        exit 3
    }
}

# ---- 步骤 3：验证到 K1 的三层可达性 ----
Write-Host ("→ 探测 {0}" -f $TargetIP)
$ok = Test-Connection -ComputerName $TargetIP -Count 2 -Quiet -ErrorAction SilentlyContinue
if ($ok) {
    Write-Host '   ICMP 通' -ForegroundColor Green
} else {
    Write-Host '   ICMP 不通（K1 可能关了 ping 响应，继续测 22 端口）' -ForegroundColor Yellow
}

$ssh = Test-NetConnection -ComputerName $TargetIP -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($ssh) {
    Write-Host '   TCP 22 可达 —— 可以直接 ssh root@192.168.88.10' -ForegroundColor Green
    Write-Host '结论：通道可用' -ForegroundColor Green
    exit 0
}

Write-Host '   TCP 22 不可达' -ForegroundColor Red
Write-Host '结论：通道不可用。PC 与 3568 已点对点，但 3568 没有把流量转给 K1，' -ForegroundColor Red
Write-Host '      需要 3568 侧开 NAT/路由（OpenHarmony 未必有 iptables）。' -ForegroundColor Red
exit 4
