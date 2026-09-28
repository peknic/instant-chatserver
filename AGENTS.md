# AGENTS.md

聊天服务器（chatser）项目说明。

## 项目概览

- 技术栈：Python 3.10+ / FastAPI / SQLAlchemy 2.x（同步）/ SQLite / Pydantic v2
- 运行环境：conda env `chatser`（Python 3.12）
- 边界：单进程 SQLite，不引入 Redis / MySQL / Kafka 等额外组件

## 功能现状

- 用户：邮箱 + 验证码 + 密码注册，用户名或邮箱登录，Bearer token（`GET /auth/me`）。
  白名单域名、`+别名`归一化、拒绝本地部分带点；验证码 30s 冷却 / 300s 有效 / 一次性。
- 会话：统一为 Chat（`members` 2 人=两人聊，>=3 人=群聊）。创建者为首个管理员；仅管理员可
  删除会话、移出他人（移出自己=退群）；邀请任意成员可调且幂等；管理员列表为空时自动提权。
- 消息：REST 分页查询 + WebSocket 实时广播；离线增量同步；前端分页「加载更多」。
- 前端：FastAPI 直接提供的原生 HTML/CSS/JS 测试客户端；页面访问密码门禁；只能登录后建连。

## 目录结构

```
chatser/
├── AGENTS.md / requirements.txt / opencode.json / chat.db（运行生成，可删）
├── .env                 # RESEND_API_KEY 等（受 opencode 权限保护，不读取）
├── app/
│   ├── __init__.py      # load_dotenv() 加载项目根 .env
│   ├── database.py      # 引擎、SessionLocal、Base、get_db
│   ├── models.py        # User / Chat / Message
│   ├── schemas.py       # Pydantic 模型
│   ├── main.py          # FastAPI 应用、启动建表+迁移、挂载路由与静态页
│   ├── ws.py            # ConnectionManager + /ws?token=
│   ├── auth/            # email_whitelist / verifycode / mailer / security / deps
│   ├── static/          # index.html / style.css / app.js（原生 JS 测试客户端）
│   └── routers/         # users.py / auth.py / chats.py
└── tests/               # conftest.py / test_basic.py / test_api.py / test_auth.py / test_websocket.py
```

## 依赖

`requirements.txt`：fastapi>=0.110、uvicorn[standard]>=0.29、websockets>=12、sqlalchemy>=2.0、
pydantic>=2.0、resend>=2、python-dotenv>=1.0。
已装：fastapi 0.141.1、pydantic 2.13.5、sqlalchemy 2.1.1、uvicorn 0.54.0、websockets 17.1、
resend 2.48.0、python-dotenv 1.2.3、pytest 9.1.1、httpx 0.28.1。运行前需在项目根放 `.env`。

## 数据库（SQLite `chat.db`，3 张表）

不建 `chat_members` 表，不区分私聊 / 群聊，统一为 Chat。

- **users**：`id`、`username`(唯一非空索引)、`email`(唯一可空索引；新账号必填)、
  `password_hash`(可空，PBKDF2-SHA256)、`avatar`、`created_at`
- **chats**：`id`、`name`、`avatar`、`members`(JSON 列表非空默认 `[]`)、
  `adminmembers`(JSON 列表非空默认 `[]`，创建者为首个)、`created_at`
- **messages**：`id`、`chat_id`(FK→chats.id CASCADE 非空索引)、`sender_id`(FK→users.id CASCADE)、
  `content`(Text 非空)、`message_type`(默认 `"text"`)、`created_at`

## 关键实现

- **database.py**：数据库地址 `CHAT_DATABASE_URL`（默认 `sqlite:///./chat.db`）；SQLite 引擎带
  `check_same_thread=False`；同步 SQLAlchemy，`get_db()` 依赖注入。
- **main.py**：`lifespan` 启动执行 `create_all`，再跑 `_migrate_chat_adminmembers()`（旧库缺
  `adminmembers` 列则 `ALTER TABLE ADD COLUMN` 并回填为 `members` 第一个，幂等）。挂载 `/static`
  与 auth/users/chats/ws 四个 router；路由 `GET /`（测试页）、`GET /api/info`、`GET /health`、
  `GET /tables`。HTTP 中间件 `disable_asset_caching` 给 `GET /` 加 `Cache-Control: no-store`、
  给 `/static/*` 加 `no-cache`，防止浏览器 / Cloudflare 缓存旧 `app.js`（旧版曾连 `/ws/{user_id}`
  导致 403）。
- **auth/**：`email_whitelist`（白名单 / `normalize_email` / 拒带点）；`verifycode`（JSON 文件存码，
  路径 `CHAT_VERIFY_CODE_FILE` 可覆盖）；`mailer`（resend 发 6 位码，`CHAT_MAIL_FROM` 可覆盖）；
  `security`（PBKDF2 哈希；HMAC-SHA256 无状态 token，payload `{uid,exp}`，密钥 `CHAT_SECRET_KEY`，
  TTL `CHAT_TOKEN_TTL`）；`deps`（`user_from_token` / `get_current_user`，REST 与 WS 共用）。
- **鉴权**：除注册/登录/发码/探活外，所有业务接口（`/users`、`/chats`、`/ws`）均要求
  `Authorization: Bearer <token>`；服务端从 token 解析当前用户，不再接受显式 `user_id` / `requester_id`。
- **chats.py 权限规则**：`requester_id` / `user_id` 一律取自 `get_current_user().id`。
  - `POST /chats`：members 去重排序、>=2 人、用户须存在、当前用户须为成员并成为首个管理员；
    两人 Chat 命中已有则幂等返回 200，否则 201。
  - 邀请 / 直接加成员：任意现有成员可调；被邀请人须存在（否则 400）；已存在成员幂等忽略；新成员非管理员。
  - 移出成员：当前用户须为成员；移出他人须管理员（移出自己=退群不受限）；目标不在群 404；
    同步清理 `adminmembers`，空则提升 `min(members)` 为管理员。
  - 删除 Chat：当前用户须为管理员；显式先删该 Chat 全部 messages 再删 Chat。
  - 消息查询：非成员 403，返回 `{items,total,limit,offset}`（id 升序）。
- **ws.py**：连接 `/ws?token=<token>`，用 `user_from_token` 校验，无效或过期先 accept 再
  `close(1008)`。兜底路由 `/ws/{path:path}` 接住旧地址（如 `/ws/5`）：先 accept、回 `error` 再
  `close(1008)`，让旧客户端停止自动重连，避免反复 403。
  `ConnectionManager.active: dict[user_id, set[WebSocket]]`（一人多连接）；发消息校验
  成员→写库→广播该 Chat 全部在线成员（含发送者）；`ping`→`pong`；错误只回发送者 `error`；
  邀请 / 移出 / 删除由 chats 调 `send_to_user` 推 `chat_invite` / `chat_removed` / `chat_deleted`；
  断线或发送失败即摘除连接。
- **static/app.js**：`ACCESS_PASSWORD = "159357"` 页面门禁（通过后写 `sessionStorage` 键
  `chatser_unlocked`，仅前端校验，非安全边界）；`api()` 自动带 `Authorization` 头；`connect()` 用
  `localStorage` token 连 `/ws?token=`（OPEN/CONNECTING 忽略重复，断线自动重连最多
  `MAX_RECONNECT_ATTEMPTS = 3` 次、失败显示 header「重连」按钮手动重连，20s ping 保活，
  `shutdown()` 主动停止）；`bootstrap()` 调 `restoreSession()` 用本地 token 请求 `/auth/me` 恢复登录，
  失败则清 token 提示重登；`logout()` 断开并清 userId/chats/未读/窗口与 token；
  连接后 `refreshChats()` + `syncMissedMessages()`（`lastSeen` in localStorage，chatId→最大 message id，
  增量补齐）；`loadInitialMessages()` 先探 total 取最近 `INITIAL_MESSAGE_LIMIT = 30` 条，
  `loadMoreMessages()` 按 offset 往回 prepend（去重 + 保持滚动）；`sendMessage()` 按消息 id 去重；
  非当前 Chat 计未读。静态资源用 `?v=16` 缓存穿透（配合服务端 `no-cache` 头）。原生 JS，无框架。

## 接口调用（给外部软件）

基地址：本机手动验证 `http://127.0.0.1:5000`，对外 `--host 0.0.0.0 --port 8000`；JSON 收发。
鉴权：除注册/登录/发码/探活外，业务接口都需 `Authorization: Bearer <token>`，服务端据此确定当前用户，
不再传 `user_id` / `requester_id`。在线 WS：`ws://<host>/ws?token=<token>`。Swagger：`GET /docs`、`GET /openapi.json`。

| 方法 | 路径 | 关键参数 | 说明 | 状态码 |
| --- | --- | --- | --- | --- |
| GET | `/api/info`、`/health`、`/tables` | - | 探活 / DB / 表名 | 200 |
| GET | `/users/{user_id}` | Bearer | 查用户 | 200/401/404 |
| POST | `/auth/send_code` | `{email, username?}` | 发验证码 | 200/400/429 |
| POST | `/auth/register` | `{username,email,password,code}` | 注册，返回 token | 201/400/409 |
| POST | `/auth/login` | `{account,password}` | account 用户名或邮箱 | 200/401 |
| GET | `/auth/me` | Bearer | 当前用户 | 200/401 |
| POST | `/chats` | Bearer；`{name?,avatar?,members[]}` | 建会话（>=2，去重） | 201/200/400/401 |
| GET | `/chats` | Bearer | 当前用户所在会话 | 200/401 |
| GET | `/chats/{id}` | Bearer | 单个会话（须为成员） | 200/401/403/404 |
| POST | `/chats/{id}/invite` | Bearer；`{members[]}` | 邀请成员 | 200/400/401/403/404 |
| POST | `/chats/{id}/members` | Bearer；`{members[]}` | 直接加成员 | 200/400/401/403/404 |
| DELETE | `/chats/{id}/members/{uid}` | Bearer | 移出/退群（移他人需管理员） | 200/401/403/404 |
| DELETE | `/chats/{id}` | Bearer | 删除会话及消息（管理员） | 200/401/403/404 |
| GET | `/chats/{id}/messages` | Bearer；`limit=50,offset=0` | 历史消息分页 | 200/401/403/404 |

返回对象：`UserRead{id,username,avatar,email,created_at}`、
`ChatRead{id,name,avatar,members[],adminmembers[],created_at}`、
`MessageRead{id,chat_id,sender_id,content,message_type,created_at}`、
`MessagePage{items,total,limit,offset}`、登录/注册 `{token,user}`。
错误统一 `{"detail": "<原因>"}`：400 参数/用户不存在、401 token 无效、403 非成员、404 不存在、
409 冲突、429 发码过快。

### curl 示例

```bash
BASE=http://127.0.0.1:5000
# 注册（先发码，再从邮箱取码；id 设为 1/2/3）
curl -s -X POST $BASE/auth/send_code -H 'Content-Type: application/json' \
  -d '{"email":"alice@gmail.com","username":"alice"}'
TOKEN=$(curl -s -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"alice","email":"alice@gmail.com","password":"secret123","code":"<码>"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"
# 建会话（当前用户成为首个管理员）
curl -s -X POST "$BASE/chats" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"项目群","members":[1,2]}'
# 邀请 3
curl -s -X POST "$BASE/chats/1/invite" -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"members":[3]}'
# 读会话 / 历史消息
curl -s "$BASE/chats/1" -H "$AUTH"
curl -s "$BASE/chats/1/messages?limit=50&offset=0" -H "$AUTH"
# 移出 3（移出自己即退群）
curl -s -X DELETE "$BASE/chats/1/members/3" -H "$AUTH"
# 删除会话（管理员）
curl -s -X DELETE "$BASE/chats/1" -H "$AUTH"
# 登录拿 token
curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"account":"alice","password":"secret123"}'
curl -s $BASE/auth/me -H "Authorization: Bearer <token>"
```

### Python 示例

```python
import json, requests, websocket   # pip install requests websocket-client

BASE = "http://127.0.0.1:5000"

def register(name, email, code):
    requests.post(f"{BASE}/auth/send_code", json={"email": email, "username": name})
    r = requests.post(f"{BASE}/auth/register", json={
        "username": name, "email": email, "password": "secret123", "code": code})
    r.raise_for_status()
    return r.json()["user"]["id"], r.json()["token"]

alice, alice_token = register("alice", "alice@gmail.com", "<码>")
bob, bob_token = register("bob", "bob@gmail.com", "<码>")
ah = {"Authorization": f"Bearer {alice_token}"}
chat = requests.post(f"{BASE}/chats", headers=ah,
                     json={"members": [alice, bob]}).json()

# WebSocket 收发
ws = websocket.create_connection(f"ws://127.0.0.1:5000/ws?token={bob_token}")
ws.send(json.dumps({"type": "message", "chat_id": chat["id"], "content": "大家好"}))
event = json.loads(ws.recv())   # {"type":"message", ...}
ws.close()
```

## WebSocket 协议

连接 `ws://<host>/ws?token=<token>`（token 无效/过期先 accept 再 close code `1008`）。

- 客户端→服务端：`{"type":"ping"}`；`{"type":"message","chat_id","content","message_type"?}`
  （非成员只回 `error`）。
- 服务端→客户端：`message`（该 Chat 全部在线成员，含发送者）、`chat_invite{chat_id,invited_by,chat}`、
  `chat_removed{chat_id,removed_by}`、`chat_deleted{chat_id,deleted_by}`、`pong`、`error{detail}`。
- 一人可多连接，消息在所有连接收到；邀请/移出/删除只在目标当时在线时推送，离线者靠
  `GET /chats`（带 token）感知变化。

## 验证结果

- `python -m pytest`：29 passed（test_basic 启动/SQLite/建表/CRUD；test_api 注册建号/鉴权/去重/
  权限/管理员/邀请/移出/删除/分页；test_auth 发码冷却/注册登录/token/邮箱规则；test_websocket 广播/
  邀请/移出/删除通知/坏 token 拒绝/断线清理）。`node --check app/static/app.js` 通过。
- 启动后：`/health` → `{"status":"healthy","database":"connected"}`；`/tables` →
  `{"tables":["chats","messages","users"]}`；`/` 返回测试页；`/static/app.js`、`style.css` 可访问。
  实测旧地址 `/ws/5` 得到 accept + `error` + `close(1008)`（不再 403），`/ws?token=<有效>` 可 ping/pong。
- 测试页：先输访问密码 `159357` → 注册/登录建立 WS；换号登录先断旧连接；「退出登录」清本地 token。
- 排障：若日志出现 `WebSocket /ws/<数字>`，说明客户端仍在跑旧 `app.js`（旧版连 `/ws/{user_id}`）；
  服务端兜底路由会 accept 后回 `error` 并 `close(1008)`（旧客户端据此停止自动重连，不再刷 403）。
  服务端已加 `no-store` / `no-cache` 头，但已缓存的浏览器需强刷（Ctrl+Shift+R）、清站点数据或清
  Cloudflare 缓存一次才会加载新 `app.js`。新客户端应连 `/ws?token=<token>`。

## 常用命令

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
uvicorn app.main:app --host 127.0.0.1 --port 5000   # 本机手动验证
python -m pytest                                     # 必须用 python -m，否则 No module named 'app'
```

## 下一阶段待办（未实现）

- 未读数、消息已读回执
- 前端界面美化
