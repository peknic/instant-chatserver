# instant-chatserver

一个轻量的即时聊天服务器：邮箱验证码注册、用户名/邮箱登录、统一会话模型（两人聊/群聊）、
REST + WebSocket 实时收发，内置原生 HTML/CSS/JS 测试客户端。

技术栈：Python 3.10+ / FastAPI / SQLAlchemy 2.x（同步）/ SQLite / Pydantic v2。
单进程 SQLite，不依赖 Redis / MySQL / Kafka 等外部组件。

## 功能特性

### 用户与鉴权
- 邮箱验证码 + 密码注册；邮箱白名单校验，`+别名` 归一化，拒绝本地部分带点。
- 验证码 30s 冷却 / 300s 有效 / 一次性，通过 Resend 发送 6 位码。
- 用户名或邮箱登录，签发 Bearer token（HMAC-SHA256 无状态，payload `{uid,exp}`，可配 TTL）。
- 除注册/登录/发码/探活外，所有业务接口与 WebSocket 均需 token，服务端据此确定当前用户。

### 会话（Chat）
- 统一为 Chat：`members` 2 人=两人聊，`>=3` 人=群聊，不建额外的成员表。
- 创建者为首个管理员；管理员可删除会话、移出他人；移出自己即退群。
- 邀请/直接加成员任意成员可调且幂等；管理员列表为空时自动把 `min(members)` 提为管理员。
- 两人 Chat 重复创建幂等返回。

### 消息
- REST 分页查询历史消息（`limit`/`offset`，id 升序）。
- WebSocket 实时广播给该 Chat 全部在线成员（含发送者）。
- 离线增量同步：客户端记录 `lastSeen`（chatId→最大消息 id），重连后补齐。
- 前端分页「加载更多」，按 offset 往回 prepend，去重并保持滚动位置。
- 一人可多连接，消息在全部连接收到；`ping`/`pong` 保活。

### 前端测试客户端
- FastAPI 直接提供的原生 HTML/CSS/JS 页面，页面访问密码门禁（仅前端校验）。
- 登录后建立 WS，断线自动重连（最多 3 次，失败可手动重连），未读计数，退出登录清本地状态。
- 静态资源带版本号 + 服务端 `no-store`/`no-cache` 头，避免缓存旧脚本。

## 项目结构

```
instant-chatserver/
├── AGENTS.md / README.md / requirements.txt / opencode.json
├── .env                      # RESEND_API_KEY 等（不入库）
├── chat.db                   # SQLite 数据文件（运行生成，不入库）
├── app/
│   ├── __init__.py           # load_dotenv() 加载项目根 .env
│   ├── database.py           # 引擎、SessionLocal、Base、get_db
│   ├── models.py             # User / Chat / Message
│   ├── schemas.py            # Pydantic 模型
│   ├── main.py               # FastAPI 应用、启动建表+迁移、挂载路由与静态页
│   ├── ws.py                 # ConnectionManager + /ws?token=
│   ├── auth/
│   │   ├── email_whitelist.py  # 白名单 / normalize_email / 拒带点
│   │   ├── verifycode.py       # JSON 文件存码
│   │   ├── mailer.py           # Resend 发码
│   │   ├── security.py         # PBKDF2 哈希 + HMAC token
│   │   └── deps.py             # user_from_token / get_current_user
│   ├── static/               # index.html / style.css / app.js
│   └── routers/              # users.py / auth.py / chats.py
└── tests/                    # conftest.py / test_basic.py / test_api.py / test_auth.py / test_websocket.py
```

## 数据库

SQLite 单文件，3 张表（不建 `chat_members` 表）：

- **users**：`id`、`username`(唯一)、`email`(唯一可空)、`password_hash`(PBKDF2-SHA256)、`avatar`、`created_at`
- **chats**：`id`、`name`、`avatar`、`members`(JSON)、`adminmembers`(JSON)、`created_at`
- **messages**：`id`、`chat_id`(FK CASCADE)、`sender_id`(FK CASCADE)、`content`、`message_type`、`created_at`

启动时自动 `create_all` 并执行幂等迁移（旧库缺 `adminmembers` 列时补齐并回填）。

## 快速开始

### 1. 环境准备

需要 Python 3.10+（推荐 conda env `chatser`，Python 3.12）：

```bash
conda create -n chatser python=3.12 -y
conda activate chatser
pip install -r requirements.txt
```

依赖：`fastapi>=0.110`、`uvicorn[standard]>=0.29`、`websockets>=12`、`sqlalchemy>=2.0`、
`pydantic>=2.0`、`resend>=2`、`python-dotenv>=1.0`。

### 2. 配置环境变量

在项目根创建 `.env`：

```dotenv
RESEND_API_KEY=re_xxxxxxxx
# 可选，均有默认值
CHAT_SECRET_KEY=change-me
CHAT_TOKEN_TTL=604800
CHAT_DATABASE_URL=sqlite:///./chat.db
CHAT_VERIFY_CODE_FILE=app/auth/verify_codes.json
CHAT_MAIL_FROM=Your Name <no-reply@example.com>
```

### 3. 启动

```bash
# 开发
uvicorn app.main:app --reload

# 本机手动验证
uvicorn app.main:app --host 127.0.0.1 --port 5000

# 对外
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后：

- 测试页：`http://127.0.0.1:5000/`（访问密码 `159357`）
- API 文档：`/docs`、`/openapi.json`
- 探活：`/health` → `{"status":"healthy","database":"connected"}`

### 4. 测试

```bash
python -m pytest        # 必须带 -m，否则找不到 app 模块
```

## 部署

### 生产运行（Uvicorn + systemd）

```ini
# /etc/systemd/system/instant-chatserver.service
[Unit]
Description=instant-chatserver
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/instant-chatserver
EnvironmentFile=/opt/instant-chatserver/.env
ExecStart=/opt/conda/envs/chatser/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now instant-chatserver
```

### 反向代理与 HTTPS/WSS

建议把 Uvicorn 只监听 `127.0.0.1`，由反向代理（Nginx / Caddy / Cloudflare Tunnel）对外提供
HTTPS，并转发 WebSocket。Nginx 关键配置：

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

使用 Cloudflare 时需确保 WebSocket 已开启；若更新过前端脚本仍加载旧版本，清一次 Cloudflare 缓存。

### 注意事项

- `.env`、`chat.db` 等含密钥或运行数据，禁止提交，已在 `.gitignore` 忽略。
- 默认验证码文件为 `app/auth/verify_codes.json`，建议一并加入 `.gitignore` 或通过 `CHAT_VERIFY_CODE_FILE` 指到仓库外。
- SQLite 单进程，保持单 worker；如需横向扩展需改用支持多实例的存储与消息总线。
- 页面访问密码仅为前端门禁，不是安全边界；真正的鉴权以 Bearer token 为准。

## API 概览

基地址示例 `http://127.0.0.1:8000`，JSON 收发；业务接口需 `Authorization: Bearer <token>`，
服务端据此确定当前用户，不再传 `user_id` / `requester_id`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/info`、`/health`、`/tables` | 探活 / DB / 表名 |
| GET | `/users/{user_id}` | 查用户 |
| POST | `/auth/send_code` | 发验证码 `{email, username?}` |
| POST | `/auth/register` | 注册，返回 token |
| POST | `/auth/login` | 登录，account 为用户名或邮箱 |
| GET | `/auth/me` | 当前用户 |
| POST | `/chats` | 建会话（`members>=2`，去重） |
| GET | `/chats` | 当前用户所在会话 |
| GET | `/chats/{id}` | 单个会话（须为成员） |
| POST | `/chats/{id}/invite` | 邀请成员 |
| POST | `/chats/{id}/members` | 直接加成员 |
| DELETE | `/chats/{id}/members/{uid}` | 移出/退群（移他人需管理员） |
| DELETE | `/chats/{id}` | 删除会话及消息（管理员） |
| GET | `/chats/{id}/messages` | 历史消息分页（`limit=50&offset=0`） |

错误统一为 `{"detail": "<原因>"}`：400 参数/用户不存在、401 token 无效、403 非成员、
404 不存在、409 冲突、429 发码过快。

## WebSocket 协议

连接 `ws://<host>/ws?token=<token>`（token 无效/过期先 accept 再 `close(1008)`）。

- 客户端→服务端：`{"type":"ping"}`；`{"type":"message","chat_id","content","message_type"?}`
- 服务端→客户端：`message`、`chat_invite`、`chat_removed`、`chat_deleted`、`pong`、`error`
- 邀请/移出/删除只在目标当时在线时推送，离线者通过 `GET /chats` 感知变化。

## License

MIT
