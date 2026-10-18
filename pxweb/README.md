# pxweb

仿 rika-ai.com 前端的**像素素材工具**，纯标准库（`http.server`），零依赖。

本仓库分两块能力：

| 能力 | 位置 | 说明 |
| --- | --- | --- |
| 本地图像处理 | `server.py` + `../pxkit` | 缩放、还原真实分辨率、调色板、精灵表等，全部在本地算，不联网 |
| 模型生成（配置） | `server.py` + `rika.py` | **直接转发 rika 原后端**，本地不重训、不降级 |

> 生成能力的开关就在设置页：填好 `API Key` 即可用；不填则只有本地处理能力，互不影响。

## 启动

```bash
python server.py                 # 默认 127.0.0.1:8765，自动开浏览器
python server.py --port 9000 --no-open
```

## 自定义模型配置页

点顶栏「⚙ 设置」或导航「设置」打开。

### 字段

| 字段 | 用途 | 默认 |
| --- | --- | --- |
| 后端地址 `base_url` | rika 后端根地址，可换成自建镜像/代理 | `https://api.rika-ai.com/v1/api` |
| API Key `api_key` | 以 `X-API-Key` 头发送 | 空 |
| 模型覆盖 `model` | 可选，写进请求体的 `model` 字段；后端不认就忽略 | 空（用后端默认） |
| 请求超时 `timeout` | 单次 HTTP 超时（秒） | `120` |
| 轮询间隔 `poll_interval` | 查 `/jobs` 的间隔（秒） | `4` |
| 轮询上限 `poll_timeout` | 轮询总时长上限（秒） | `900` |

两个安全约定：

- **API Key 只写不回读**。`GET /api/config` 只给脱敏形式（如 `sk-t••••••••••••abcd`）；页面上留空保存表示「保持原值不变」。
- `config.json` 已在 `.gitignore` 里，不会被提交。

### 直接对接的原后端端点

| kind | 路径 | 积分 |
| --- | --- | --- |
| `v1` | `POST /generate` | 4 |
| `v2` | `POST /generate-v2` | 6 |
| `character` | `POST /generate-character` | 1 |
| `item` | `POST /generate-item` | 1 |
| `map` | `POST /generate-map` | 2 |

加上 `GET /credits`（余额）与 `GET /jobs/{gen_id}`（任务状态与结果）。

## HTTP 接口

```
GET  /                       前端页面
GET  /static/*               静态资源
GET  /work/<id>.png          处理结果
POST /api/open               导入图片（dataURL）→ {key, w, h}
POST /api/op                 执行操作 → {key, w, h, info, palette?, tiles?}

GET  /api/config             读取配置（api_key 已脱敏）
POST /api/config             保存配置
POST /api/config/test        用候选配置测连通性 → {ok, credits}
POST /api/credits            查询积分余额
POST /api/gen                提交生成任务 → {gen_id, kind, credits}
POST /api/job                查询任务 {gen_id, wait?} → {job}
```

`rika.RikaError` 的错误会原样映射成中文并带上原状态码（401/402/429 等），前端直接显示在设置页底部状态栏。

## 目录

```
pxweb/
├── server.py       HTTP 服务（本地处理 + 生成代理）
├── rika.py         rika 后端客户端（配置读写 + 转发）
├── config.json     运行时生成，含 API Key，勿提交
├── static/
│   ├── index.html
│   ├── app.css
│   └── app.js
└── work/           处理中间产物
```
