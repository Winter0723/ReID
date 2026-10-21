# 游客联调：启动、手动验收与停止

适用于完整三端源码。以下命令在 **Windows PowerShell** 执行，仓库路径请替换成实际检出位置。先启动隔离测试栈，再启动运营前端，最后按后端 → 电脑游客端 → 手机的顺序验收。生产配置和人工付款模式见 [游客 H5 说明](VISITOR_H5.md)。

## 1. 环境、服务和端口

| 服务 | 启动位置 | 地址或作用 |
| --- | --- | --- |
| 后端 API | Docker `backend` | `http://127.0.0.1:22889` |
| 渲染任务进程 | Docker `worker` | 消费测试队列，必须保持运行 |
| 游客制作协调器 | Docker `visitor-coordinator` | 推进匹配和制作状态；挑选与编辑由游客确认 |
| 游客 H5 和同源代理 | Docker `visitor-web` | `http://127.0.0.1:25174/visitor/` |
| 运营浏览器前端 | 本机 Vite | `http://127.0.0.1:5173` |
| 运营桌面前端，可选 | 本机 Tauri | 桌面窗口，与运营浏览器版二选一 |
| PostgreSQL、Redis、MinIO、Milvus、etcd | Docker 自动启动 | 独立测试数据库、素材、向量和队列 |

测试项目固定为 `myreid-visitor-test`，数据卷和网络独立。一直使用本文的 `--env-file`、`-p` 和 `-f` 参数，不叠加生产 Compose 文件。默认同时模拟登录与付款，登记、识别、制作、订单及下载仍使用真实流程。

准备 Docker Desktop（Linux containers）、Node.js 22/npm、Git。桌面前端还需要原项目的 Rust、Windows 构建工具和 WebView2 环境。Docker Compose 必须支持 `!override`，遇到 unknown tag 时更新 Docker Desktop / Compose。

```powershell
Set-Location 'C:\path\to\MyReID'
git branch --show-current
git status --short
docker version
docker compose version
node --version
npm.cmd --version
```

使用已验收的源码版本，按 [模型说明](../model/README.md) 获取三个模型后执行：

```powershell
python scripts/check_models.py
Get-Item .\FastAPIBackend\yolov8n.pt, .\model\ViT-MSMT17-60.pth, .\model\clip_weights\ViT-B-16.pt |
    Select-Object FullName, Length
```

三个模型必须是实际权重，不能只是 Git LFS 指针。首次构建会下载真实识别和跟踪依赖，需要网络、磁盘及足够内存。本文默认 CPU 配置，制作较慢，不会自动使用现有生产 GPU 容器。

## 2. 启动全部后端及游客 H5

在仓库根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-visitor-test.ps1
```

首次运行会生成被 Git 忽略的 `.env.visitor-test`，自动生成会话密钥和邀请码；后续保留原配置。此命令同时启动数据库、队列、存储、API、worker、协调器及游客页面，无需再启动本机 Python API。后端启动时自动执行原有 core/scenic 数据库迁移。

查看本机测试邀请码：

```powershell
Select-String -Path .\.env.visitor-test -Pattern '^VISITOR_TEST_INVITE_CODE='
```

为后面的命令定义 Compose 参数。**每开一个新 PowerShell 终端，都重新执行这一段**：

```powershell
Set-Location 'C:\path\to\MyReID'
$TestCompose = @(
    '--env-file', '.env.visitor-test',
    '-p', 'myreid-visitor-test',
    '-f', 'docker-compose.yml',
    '-f', 'docker-compose.visitor-test.yml',
    '--profile', 'visitor'
)
docker compose @TestCompose ps -a
```

预期 backend 健康，worker、visitor-coordinator、visitor-web 运行。一次性建库任务 `database-init` 显示 `Exited (0)` 是正常情况。

```powershell
# 查看最近日志
docker compose @TestCompose logs --tail 100 backend worker visitor-coordinator visitor-web
# 持续看日志，Ctrl+C 只退出日志查看，不停止容器
docker compose @TestCompose logs -f --tail 100 backend worker visitor-coordinator
# 下次启动，或修改环境配置后应用
docker compose @TestCompose up -d
# 修改源码后重新构建并应用
docker compose @TestCompose up -d --build backend worker visitor-coordinator visitor-web
```

`restart` 不读取新的环境变量；修改 `.env.visitor-test` 后使用 `up -d`。

## 3. 手动验证后端

### 3.1 健康、模型、队列和配置

```powershell
$Api = 'http://127.0.0.1:22889'
$VisitorOrigin = 'http://127.0.0.1:25174'
Invoke-RestMethod "$Api/api/v1/healthz"
Invoke-RestMethod "$Api/api/v1/readyz" | ConvertTo-Json -Depth 8
Invoke-RestMethod "$VisitorOrigin/api/v1/scenic/visitor/runtime"
Start-Process "$Api/docs"
```

验收标准：

- `healthz` 返回 `status=ok`、开发环境。
- `readyz` 的数据库、景区库、队列、向量库和模型可用，`degraded_reasons` 无未处理故障。首次模型加载需要时间；仅 healthz 成功不代表可以制作视频。
- runtime 返回 `auth_provider=test`、`payment_provider=test`、`testing=true`，不暴露邀请码和密钥。
- Swagger 从后端 `22889/docs` 访问；游客 `25174` 只代理游客 API，访问 `/docs` 返回 404 正常。

### 3.2 准备测试工作人员和景区

优先按第 4 节启动运营前端，进入景区业务，注册测试工作人员、创建景区，在“游客自助服务”保存开放状态、授权文字、版本和价格。测试库是新库，不会自动出现原生产账号及景区。

默认 `EMAIL_CODE_DEV_MODE=1`，验证码请求响应含 `debug_code`，可从开发者工具 Network 查看，不必等待真实邮件。也可以通过 API 创建账号及景区：

```powershell
$Api = 'http://127.0.0.1:22889'
$Username = Read-Host '新的测试用户名，至少 3 字符'
$Email = Read-Host '测试邮箱地址'
$PasswordInput = Read-Host '测试密码，至少 6 字符' -AsSecureString
$Password = [System.Net.NetworkCredential]::new('', $PasswordInput).Password
$Code = Invoke-RestMethod "$Api/api/v1/scenic/auth/email-code/request" -Method Post -ContentType 'application/json' -Body (@{
    email = $Email; username = $Username; purpose = 'register'
} | ConvertTo-Json)
$Token = Invoke-RestMethod "$Api/api/v1/scenic/auth/register" -Method Post -ContentType 'application/json' -Body (@{
    username = $Username; email = $Email; password = $Password; code = $Code.debug_code
} | ConvertTo-Json)
$Password = $null
$StaffHeaders = @{ Authorization = "Bearer $($Token.access_token)" }
$Project = Invoke-RestMethod "$Api/api/v1/scenic/projects" -Method Post -Headers $StaffHeaders -ContentType 'application/json' -Body '{"name":"Visitor integration site"}'
$SiteId = $Project.id
$ServiceBody = @{
    enabled = $true
    description = 'Integration test site'
    consent_text = 'I consent to matching and video production for this test visit.'
    consent_version = 'visitor-v1'
    price_cents = 1990
} | ConvertTo-Json
Invoke-RestMethod "$Api/api/v1/scenic/projects/$SiteId/visitor-service" -Method Put -Headers $StaffHeaders -ContentType 'application/json' -Body $ServiceBody
Invoke-RestMethod "$Api/api/v1/scenic/visitor/sites/$SiteId"
```

用户名、邮箱须未被注册；已经创建过的账号直接从运营界面登录。`1990` 单位是分，即测试金额 19.90 元。后续调价不会改变已经登记的游览售价。

### 3.3 登录与会话隔离

以下命令针对默认电脑 HTTP 配置。如果已经换手机地址，同步修改 `$VisitorOrigin`。首先填入实际景区 ID：

```powershell
$SiteId = 1 # 替换成实际景区 ID，不要盲目使用 1
$Api = 'http://127.0.0.1:22889'
$VisitorOrigin = 'http://127.0.0.1:25174'
$VisitorHeaders = @{ 'X-Visitor-Request' = '1'; Origin = $VisitorOrigin }
$InviteLine = Get-Content .\.env.visitor-test | Where-Object { $_ -match '^VISITOR_TEST_INVITE_CODE=' } | Select-Object -First 1
$Invite = $InviteLine.Substring('VISITOR_TEST_INVITE_CODE='.Length)
$SessionA = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
$SessionB = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
Invoke-RestMethod "$Api/api/v1/scenic/visitor/auth/test" -Method Post -WebSession $SessionA -Headers $VisitorHeaders -ContentType 'application/json' -Body (@{
    project_id = $SiteId; visitor = 'A'; invite_code = $Invite
} | ConvertTo-Json)
Invoke-RestMethod "$Api/api/v1/scenic/visitor/auth/test" -Method Post -WebSession $SessionB -Headers $VisitorHeaders -ContentType 'application/json' -Body (@{
    project_id = $SiteId; visitor = 'B'; invite_code = $Invite
} | ConvertTo-Json)
Invoke-RestMethod "$Api/api/v1/scenic/visitor/me" -WebSession $SessionA
Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits" -WebSession $SessionA
Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits" -WebSession $SessionB
```

按第 5 节用 A 登记后，取实际 A 游览 ID 验证 B 无权访问：

```powershell
$VisitId = 1 # 替换为实际 A 游览 ID
try {
    Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits/$VisitId" -WebSession $SessionB
} catch {
    [int]$_.Exception.Response.StatusCode # 预期 404
}
```

同一景区的 A/B 是固定测试身份，不同浏览器登录 A 会看到同一份 A 记录。多人并行测试宜使用不同景区。邀请码错误预期 403；缺少 `X-Visitor-Request` 的写请求预期 403；没有会话读取私有记录预期 401。

### 3.4 支付状态与领取接口

先通过第 5 节真实制作，使 A 的游览达到 `ready`，再执行。不能给未制作完成的游览直接付款。

```powershell
$Checkout = Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits/$VisitId/pay" -Method Post -WebSession $SessionA -Headers $VisitorHeaders
$Checkout | ConvertTo-Json -Depth 6
$CheckoutToken = $Checkout.payment.token
Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits/$VisitId/test-payment" -Method Post -WebSession $SessionA -Headers $VisitorHeaders -ContentType 'application/json' -Body (@{
    token = $CheckoutToken; outcome = 'delayed'
} | ConvertTo-Json)
Start-Sleep -Seconds 16
$PaidVisit = Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits/$VisitId/payment-status" -Method Post -WebSession $SessionA -Headers $VisitorHeaders
$PaidVisit.order
# 重复通知，不应生成新订单或改变已付款状态
Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits/$VisitId/test-payment" -Method Post -WebSession $SessionA -Headers $VisitorHeaders -ContentType 'application/json' -Body (@{
    token = $CheckoutToken; outcome = 'duplicate'
} | ConvertTo-Json)
$PaidVisit.download_urls
# 实际保存两版视频后，再确认领取
Invoke-RestMethod "$Api/api/v1/scenic/visitor/visits/$VisitId/claim" -Method Post -WebSession $SessionA -Headers $VisitorHeaders
# 撤销这两个 PowerShell 会话
Invoke-RestMethod "$Api/api/v1/scenic/visitor/logout" -Method Post -WebSession $SessionA -Headers $VisitorHeaders
Invoke-RestMethod "$Api/api/v1/scenic/visitor/logout" -Method Post -WebSession $SessionB -Headers $VisitorHeaders
```

已付款订单再次 `/pay` 返回 `payment=null`，因此本节必须从未付款成片开始。成功、取消、失败的 outcome 分别为 `success`、`cancel`、`failure`。已到账订单不会因取消/失败退回未付款。延迟状态存在数据库，可等待期间重启 API，然后刷新验证恢复。

## 4. 运营前端启动和停止

### 4.1 浏览器版，推荐先验收

另开终端，用同源代理连接测试后端：

```powershell
Set-Location 'C:\path\to\MyReID'
$env:MYREID_API_PORT = '22889'
Remove-Item Env:MYREID_API_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:VITE_MYREID_API_BASE_URL -ErrorAction SilentlyContinue
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_tauri_frontend.ps1 -BrowserOnly
```

访问 `http://127.0.0.1:5173`，进入景区业务。浏览器版不要设置直连 `VITE_MYREID_API_BASE_URL`，否则会跳过代理导致跨域；前端 `.env*` 若有该变量也须清除并重启。这里的 `MYREID_API_PORT=22889` 才是代理目标。

脚本会在依赖不存在时自动安装。需要按锁文件重装时，先执行：

```powershell
npm.cmd --prefix .\TauriFrontend ci
```

**停止：在该启动终端按 Ctrl+C，关闭网页。** 后端和制作任务会继续运行。

### 4.2 Tauri 桌面版，可选

先停止运营浏览器版，避免 5173 端口冲突：

```powershell
Set-Location 'C:\path\to\MyReID'
$env:MYREID_API_PORT = '22889'
$env:MYREID_API_BASE_URL = 'http://127.0.0.1:22889'
$env:VITE_MYREID_API_BASE_URL = 'http://127.0.0.1:22889'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_tauri_frontend.ps1
```

**停止：关闭桌面窗口，并在开发终端按 Ctrl+C。** 若提示终止批处理，输入 Y。不要通过终止所有 node/python 进程停止，避免影响其他项目。

## 5. 电脑游客前端：手动完整流程

游客页面已由第 2 节 Docker 启动，不需要单独运行 Vite。修改页面后按第 2 节重建 `visitor-web` 镜像即可。

需要热更新时，可在 `VisitorFrontend/` 先设置 `$env:MYREID_API_PORT='22889'`，再执行 `npm ci` 和 `npm run dev`，使用 `http://127.0.0.1:5174/visitor/`。同时将 `.env.visitor-test` 中 `VISITOR_PUBLIC_BASE_URL` 改成 `http://127.0.0.1:5174`，用第 2 节的 `up -d` 更新后端配置，并统一通过该入口联调；仅改变代理目标不足以通过后端 Origin 校验。返回 Docker 网页入口时恢复 `25174` 配置。以下步骤默认继续使用 Docker 网页。

```powershell
$SiteId = 1 # 改为实际景区 ID
Start-Process "http://127.0.0.1:25174/visitor/#/sites/$SiteId"
```

| 顺序 | 手动操作 | 验收结果 |
| --- | --- | --- |
| 1 | 检查页顶，输入邀请码选择 A | 始终显示联调提示，成功登录，不跳微信 |
| 2 | 上传单人清晰照片、框选本人、同意授权 | 真实检测并登记；无人物、多人物、未授权应拒绝 |
| 3 | 运营端查看登记 | 对应景区出现记录，可核对参考照片与入园时间 |
| 4 | 相机采集或相机点位上传测试录像并完成索引 | 同景区，录像含该游客，拍摄时间处于本次游览内 |
| 5 | 游客结束游览 | 持久化出园时间，经过封存、等待素材、匹配；关页重开不丢失进度 |
| 6 | 游客预览候选并选择本人片段 | 进入 `selecting`，仅显示本次游览时间范围内的匹配片段 |
| 7 | 编辑横竖版排序、裁切、构图、原声与封面，保存后重开 | `editing` 草稿恢复；未确认制作前不直接产生成片 |
| 8 | 确认双版制作，等待完成 | `rendering` 后进入 `ready`，正常流程无需工作人员发布；失败画幅可重试 |
| 9 | 成片后试看、切横竖屏 | 两版均可播放；未付款不能获取完整视频 |
| 10 | 打开测试收银台，依次点取消、失败、延迟到账 | 取消保留订单，失败可重试付款；延迟需等 15 秒再刷新支付 |
| 11 | 到账后点重复通知 | 订单号不变，保持 paid，不重复建单 |
| 12 | 下载横竖完整视频，保存后确认领取 | 实际文件可播放、方向正确，领取状态保存 |
| 13 | 回景区入口切换 B，并打开 A 的记录地址 | B 列表无 A 记录，A 的私人记录返回 404 |
| 14 | 新增另一条 A 游览、制作，测试立即成功 | 单独验证 success 分支 |

补充异常验收：无素材或索引超时后，游客可以重新匹配，工作人员也可在异常队列协助；重新匹配不改变原入园、出园时间。一旦创建订单，即使还未付款，也不能通过重匹配、编辑或重试替换交付版本。

测试录像可使用运营端相机点位的模拟视频上传功能，对应 `POST /api/v1/scenic/projects/{project_id}/camera-points/{point_id}/simulate-video`；它只模拟输入来源，索引、匹配和制作不模拟。需要校准素材时间时，现有 `PATCH /api/v1/scenic/projects/{project_id}/media/{media_id}/editing-metadata` 支持带时区的 `recorded_at`，同时确认索引时间一致。不要给正式验收素材填写虚假拍摄时间。

Docker 内的 127.0.0.1 指向容器本身，RTSP 须是容器可达的相机地址。本机 USB 摄像头不会自动出现在 Linux 容器中；先用测试录像或可达 RTSP 验证。

延迟付款不是浏览器自动定时到账；15 秒后点击“刷新到账结果”或“已付款？刷新支付结果”。普通游览信息轮询不等于支付同步。

只停止/恢复游客前端：

```powershell
docker compose @TestCompose stop visitor-web
docker compose @TestCompose up -d visitor-web
```

## 6. 手机端启动与手动验证

手机无需编译 App，也没有另一个前端进程，只访问同一个游客 H5。手机的 127.0.0.1 不是电脑；实际游客地址与 `VISITOR_PUBLIC_BASE_URL` 必须同源。测试期间电脑也尽量使用同一个地址。

### 6.1 同 Wi-Fi HTTP 基础验收

仅用于模拟登录/付款。真实公众号授权使用下一节 HTTPS。

```powershell
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object InterfaceAlias, IPAddress
```

选择电脑 Wi-Fi/LAN 地址，例如 `192.168.1.50`，不要选 Docker/WSL 虚拟网卡。生成临时端口覆盖文件：

```powershell
New-Item -ItemType Directory -Force .\tmp | Out-Null
@'
services:
  visitor-web:
    ports: !override
      - "0.0.0.0:25174:80"
'@ | Set-Content -Encoding utf8 .\tmp\visitor-mobile-http.yml
notepad.exe .\.env.visitor-test
```

修改以下两行，同一个键只保留一行，IP 使用实际地址：

```dotenv
VISITOR_PUBLIC_BASE_URL=http://192.168.1.50:25174
VISITOR_COOKIE_SECURE=0
```

```powershell
# 先定义第 2 节的 TestCompose
$MobileCompose = $TestCompose + @('-f', 'tmp/visitor-mobile-http.yml')
docker compose @MobileCompose up -d backend worker visitor-coordinator visitor-web
```

若被 Windows 防火墙拦截，在**管理员 PowerShell**执行，仅开放本地子网：

```powershell
New-NetFirewallRule -DisplayName 'MyReID Visitor Test LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 25174 -RemoteAddress LocalSubnet -Profile Private
```

手机、电脑均打开 `http://192.168.1.50:25174/visitor/#/sites/实际ID`，重新登录 A/B。改主机地址后 Cookie 不会自动迁移。HTTP 模式下生成入园二维码可能提示要求 HTTPS，直接输入或分享这个地址即可。

手机检查：相册/拍照上传、手指框选、授权勾选、横竖屏播放、返回页面保留进度、五种支付结果、横竖视频下载、系统浏览器打开、保存后领取。若同 Wi-Fi 仍不通，检查客户端隔离、Private 网络类型及实际 IPv4。

停止手机 HTTP 入口：

```powershell
docker compose @MobileCompose stop visitor-web
```

恢复仅本机测试：先将 `.env.visitor-test` 改回 `VISITOR_PUBLIC_BASE_URL=http://127.0.0.1:25174`、`VISITOR_COOKIE_SECURE=0`，再执行：

```powershell
docker compose @TestCompose up -d backend worker visitor-coordinator visitor-web
# 管理员 PowerShell，移除本次创建的规则
Remove-NetFirewallRule -DisplayName 'MyReID Visitor Test LAN' -ErrorAction SilentlyContinue
```

### 6.2 HTTPS 微信验收

准备手机可解析、访问的测试域名和匹配域名的受信任证书。已有 HTTPS 代理时，将游客域名转发到电脑 `127.0.0.1:25174`，保留 `/visitor/` 和 `/api/v1/scenic/visitor/` 路径；不要将游客域名直接转发到完整后端 22889。

没有代理时，可用下面的独立 Nginx 容器。它使用**已经取得的证书**，不负责签发证书、配置 DNS 或公网路由。示例采用 24443，避免占用现有 443。域名和证书来源目录必须替换成自己的值。

```powershell
New-Item -ItemType Directory -Force .\tmp\visitor-mobile, .\secrets\visitor-test-tls | Out-Null
Copy-Item -LiteralPath 'C:\your-certificate-directory\fullchain.pem' -Destination .\secrets\visitor-test-tls\fullchain.pem
Copy-Item -LiteralPath 'C:\your-certificate-directory\privkey.pem' -Destination .\secrets\visitor-test-tls\privkey.pem
@'
server {
    listen 443 ssl;
    server_name visitor-test.example.com;
    ssl_certificate /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.pem;
    client_max_body_size 11m;
    location / {
        proxy_pass http://visitor-web:80;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
'@ | Set-Content -Encoding ascii .\tmp\visitor-mobile\nginx.conf
@'
services:
  visitor-test-https:
    image: nginx:stable-alpine
    ports:
      - "0.0.0.0:24443:443"
    volumes:
      - ./tmp/visitor-mobile/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./secrets/visitor-test-tls:/etc/nginx/tls:ro
    depends_on:
      - visitor-web
'@ | Set-Content -Encoding utf8 .\tmp\visitor-mobile-https.yml
notepad.exe .\tmp\visitor-mobile\nginx.conf
notepad.exe .\.env.visitor-test
```

替换 Nginx 的 `server_name` 和环境文件地址；私钥目录 secrets 已被 Git 忽略：

```dotenv
VISITOR_PUBLIC_BASE_URL=https://visitor-test.example.com:24443
VISITOR_COOKIE_SECURE=1
```

```powershell
$HttpsCompose = $TestCompose + @('-f', 'tmp/visitor-mobile-https.yml')
docker compose @HttpsCompose up -d backend worker visitor-coordinator visitor-web visitor-test-https
docker compose @HttpsCompose exec visitor-test-https nginx -t
docker compose @HttpsCompose logs --tail 50 visitor-test-https
# 替换成实际域名；不要跳过证书校验
Invoke-RestMethod 'https://visitor-test.example.com:24443/api/v1/scenic/visitor/runtime'
```

局域网 HTTPS 被防火墙拦截时，在管理员 PowerShell 开放本地子网：

```powershell
New-NetFirewallRule -DisplayName 'MyReID Visitor Test HTTPS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 24443 -RemoteAddress LocalSubnet -Profile Private
```

手机微信打开 `https://实际域名:24443/visitor/#/sites/实际ID`。完整执行第 5 节，特别测试关闭微信页面后恢复、视频播放、微信菜单“在浏览器打开”、签名下载和真实文件保存。过期链接从已购页面重新获取。

有可用公众号授权时，可改为 `VISITOR_AUTH_PROVIDER=wechat`、`VISITOR_PAYMENT_PROVIDER=test`，填写公众号 AppID/Secret 并配置授权域名。真实授权建议使用标准 443 的测试 HTTPS 入口；上面 24443 示例只验证模拟登录，不证明满足公众号域名要求。回调路径为 `/api/v1/scenic/visitor/auth/callback`。修改配置后用 `up -d` 更新后端及工作进程。

停止并移除本次 HTTPS 代理：

```powershell
docker compose @HttpsCompose stop visitor-test-https
docker compose @HttpsCompose rm -f visitor-test-https
# 管理员 PowerShell
Remove-NetFirewallRule -DisplayName 'MyReID Visitor Test HTTPS' -ErrorAction SilentlyContinue
```

恢复电脑 HTTP 时，将环境文件地址和 Cookie 设置改回默认，再用 `$TestCompose up -d` 应用。Secure Cookie 不能用于 HTTP 登录验证。

## 7. 全部停止、恢复和重启命令

先重新定义第 2 节 `$TestCompose`。若本次使用 HTTPS 覆盖，用 `$HttpsCompose` 执行整栈停止，以包含额外代理。

```powershell
# 全部暂停，保留容器及测试数据
docker compose @TestCompose stop
# 恢复全部服务 / 应用配置变动
docker compose @TestCompose up -d
# 全部停止并移除容器、网络，保留数据库和视频命名卷
docker compose @TestCompose down
# 使用 HTTPS 覆盖时，用这一条代替上面的 down
docker compose @HttpsCompose down
# 只重启后端，不重新读取环境变量
docker compose @TestCompose restart backend
# 只重启任务进程
docker compose @TestCompose restart worker visitor-coordinator
# 只暂停制作，保留 API 和游客页面
docker compose @TestCompose stop worker visitor-coordinator
# 恢复制作
docker compose @TestCompose up -d worker visitor-coordinator
```

停止整个环境前先让渲染完成；中断中的任务可能需要在原运营异常队列重试。上述 down 都**不带 `-v`**，保留测试数据。无需删除数据库即可测试新的游览。

运营前端在自己的终端按 Ctrl+C，桌面版同时关窗口；手机只需关闭页面，无额外进程停止命令。若创建过防火墙规则，按第 6 节删除。最后检查：

```powershell
docker compose @TestCompose ps -a
Get-NetTCPConnection -State Listen -LocalPort 22889,25174,5173,24443 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess
# 清除本终端的前端运行变量
Remove-Item Env:MYREID_API_PORT -ErrorAction SilentlyContinue
Remove-Item Env:MYREID_API_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:VITE_MYREID_API_BASE_URL -ErrorAction SilentlyContinue
```

若端口由其他项目占用，先确认 PID，不要批量结束所有进程。

## 8. 常见故障及验收边界

| 现象 | 定位方式 |
| --- | --- |
| 测试登录 404 / 启动拒绝 | provider、开发环境、邀请码至少 16 位，修改后 up -d |
| 运营端找不到测试景区 | 进入景区业务；浏览器代理端口 22889，重启前端 |
| 来源不匹配 403 | 地址与 VISITOR_PUBLIC_BASE_URL 同源；localhost、127.0.0.1、局域网 IP 不同源 |
| 登录后仍然 401 | HTTP 的 Secure Cookie=0，HTTPS=1；改主机地址后重新登录 |
| 登记 422 | 清晰单人照片、正确框选、授权版本；检查真实检测结果 |
| 登记 503 / 模型失败 | 三个模型文件、LFS 实体、backend 日志、readyz |
| 制作不推进 | worker、协调器、Redis、原运营异常队列 |
| 无素材或需人工处理 | 检查景区、人物、拍摄与索引时间、素材封存和索引完成情况；正常自助流程不要求启用人物跟踪 |
| 手机无法访问 | 同 Wi-Fi、正确 IPv4、端口绑定、防火墙、客户端隔离或 DNS |
| 延迟付款不变 paid | 15 秒后刷新支付结果，普通页面读取不执行支付同步 |
| 支付模式不匹配 | 已有订单绑定创建时模式；切换适配器后用新游览验收 |
| 微信未直接保存视频 | 菜单在系统浏览器打开；签名过期则从已购页刷新链接 |

记录景区 ID、游客 A/B、游览 ID、订单号、手机系统/微信版本及失败日志。下载后真正打开横竖文件，确认视频里是登记本人，而不只检查 HTTP 200。

文档中的命令依据当前代码核对，真实相机、模型制作和手机网络需现场执行。模拟联调不能证明真实微信扣款、商户权限、通知验签和真实到账配置正确，这些需商户条件齐备后另行验收。
