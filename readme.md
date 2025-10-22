<div align="center">

# 🎨 Zhenxun Bot · AI 创作插件

**智能、稳定的多模态绘图扩展**

*基于 `zhenxun_bot` 实现文生图 / 图生图能力，支持多引擎、模板和提示词工程*

---

</div>

## 📑 目录

- [✨ 快速特性](#-快速特性)
- [📦 安装与更新](#-安装与更新)
- [🍪 豆包绘图配置](#-豆包绘图配置)
- [🔌 API 绘图配置](#-api-绘图配置)
- [📖 命令速查](#-命令速查)
  - [绘图指令](#绘图指令)
  - [绘图参数](#绘图参数)
  - [模板管理](#模板管理超级用户)
- [🖼️ 生成示例](#️-生成示例)
- [⚠️ 常见错误](#️-常见错误)
- [⚙️ 插件配置项](#️-插件配置项)

---

## ✨ 快速特性

<table>
<tr>
<td width="50%">

### 🚀 多引擎绘图
- 原生支持豆包网页版
- 可切换至自定义 API（如 Gemini）
- 灵活切换，适配不同场景

</td>
<td width="50%">

### ✍️ 提示词工程
- 一键提示词润色
- 从图片逆向生成风格模板
- AI 驱动的智能优化

</td>
</tr>
<tr>
<td width="50%">

### 📋 模板管理
- 内置常用风格模板
- 支持增、删、改、查
- 重载、清空等完整操作

</td>
<td width="50%">

### 🖼️ 完备图生图
- 引用图片、附带图片
- `@用户` 头像渲染
- 多图输入支持

</td>
</tr>
<tr>
<td colspan="2">

### 🛡️ 稳定运行
- 任务队列 + 冷却控制，保障高并发下的可用性
- 智能队列管理，预估等待时间

</td>
</tr>
</table>

---

## 📦 安装与更新

### 方法一：插件商店 🛒

```bash
# 私聊机器人
插件商店
# 搜索并安装 "AI 创作" 插件
```

### 方法二：手动部署 📁

```bash
# 1. 克隆或下载插件
git clone <repository-url>

# 2. 放入插件目录
mv zhenxun-plugin-ai_creation zhenxun/plugins/

# 3. 重启机器人
python bot.py
```

---

## 🍪 豆包绘图配置

插件通过 **Playwright** 驱动豆包网页版。建议按照下列步骤配置 Cookie，以提升成功率。

> [!TIP]
> **建议配置 Cookie**
> - ⚠️ 游客模式易触发风控
> - 📊 单账号日配额约 **100 次**
> - 🔄 支持配置多个 Cookie 轮询

### 🔑 获取 Cookie

<details>
<summary>📖 点击展开详细步骤</summary>

1. **登录豆包**  
   访问 [https://www.doubao.com/chat/](https://www.doubao.com/chat/)

2. **打开开发者工具**  
   按 `F12` 键，切换到 **Network** 标签页

3. **触发请求**  
   在聊天框发送任意内容

4. **过滤请求**  
   在过滤框输入 `completion`，选择最新请求

5. **复制 Cookie**  
   在 **Headers → Request Headers** 中找到并复制 `Cookie` 完整内容

6. **写入配置**  
   将 Cookie 写入 `data/config.yaml` 的 `DOUBAO_COOKIES` 字段

</details>

**配置示例：**

```yaml
# 单个 Cookie
DOUBAO_COOKIES: "your_long_cookie_string_here..."

# 多个 Cookie（推荐）
DOUBAO_COOKIES:
  - "cookie_value_1"
  - "cookie_value_2"
  - "cookie_value_3"
```

### 🔧 常见问题排查

<table>
<tr>
<td>🐛 <b>问题</b></td>
<td>💡 <b>解决方案</b></td>
</tr>
<tr>
<td>无法登录 / 人机验证</td>
<td>将 <code>HEADLESS_BROWSER</code> 设为 <code>False</code>，观察浏览器流程并手动处理</td>
</tr>
<tr>
<td>频繁触发风控</td>
<td>配置多个有效 Cookie，启用轮询机制</td>
</tr>
<tr>
<td>生成失败</td>
<td>检查 Cookie 是否过期，尝试重新获取</td>
</tr>
</table>

---

## 🔌 API 绘图配置

使用命令 `draw -e api ...` 即可切换到 **API 引擎**。

> [!NOTE]
> **推荐配置**
> - 🎯 推荐模型：`Gemini/gemini-2.5-flash-image-preview`
> - 💰 成本控制：可使用第三方中转服务
> - 🔐 需配置：`API Base URL` + `API Key`

---

## 📖 命令速查

### 🎨 绘图指令

| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `draw [描述]` | 根据文本生成图片 | `draw 一只可爱的猫` |
| `draw [描述] [图片]` | 图文结合创作 | `draw 换成油画风格 [附带图片]` |
| `[引用图片] draw [描述]` | 回复图片并附加描述 | 引用消息后 `draw 增加夕阳背景` |

> [!TIP]
> 💡 使用 `@用户` 可自动获取对方头像作为输入，支持多用户或多图

### ⚙️ 绘图参数

| 参数 | 说明 | 可选值 |
| :--- | :--- | :--- |
| `-e`, `--engine` | 选择绘图引擎 | `doubao` / `api` |
| `-t`, `--template` | 应用预设模板 | 模板名称或序号 |
| `-o`, `--optimize` | 开/关提示词优化 | `on` / `off` |

### 📋 模板管理（超级用户）

<table>
<thead>
<tr>
<th>指令</th>
<th>作用</th>
<th>示例</th>
</tr>
</thead>
<tbody>
<tr>
<td><code>绘图模板 list</code></td>
<td>查看全部模板</td>
<td><code>绘图模板 list</code></td>
</tr>
<tr>
<td><code>绘图模板 info &lt;模板&gt;</code></td>
<td>查看模板详情</td>
<td><code>绘图模板 info 手办</code></td>
</tr>
<tr>
<td><code>绘图模板 create [图片]</code></td>
<td>🤖 AI 智能创建模板</td>
<td><code>绘图模板 create [附带图片]</code></td>
</tr>
<tr>
<td><code>绘图模板 optimize &lt;名称&gt; [指令]</code></td>
<td>🤖 AI 智能优化模板</td>
<td><code>绘图模板 optimize 监狱风格照 白色标牌</code></td>
</tr>
<tr>
<td><code>绘图模板 add &lt;名&gt; &lt;提示词&gt;</code></td>
<td>手动新增模板</td>
<td><code>绘图模板 add 油画 古典油画风格</code></td>
</tr>
<tr>
<td><code>绘图模板 del &lt;名1&gt; [名2]...</code></td>
<td>删除模板</td>
<td><code>绘图模板 del 油画 水彩</code></td>
</tr>
<tr>
<td><code>绘图模板 edit &lt;名&gt; &lt;新提示词&gt;</code></td>
<td>更新模板内容</td>
<td><code>绘图模板 edit 手办 新的提示词</code></td>
</tr>
<tr>
<td><code>绘图模板 reload</code></td>
<td>从文件重载模板</td>
<td><code>绘图模板 reload</code></td>
</tr>
<tr>
<td><code>绘图模板 clear</code></td>
<td>清空全部模板</td>
<td><code>绘图模板 clear</code></td>
</tr>
</tbody>
</table>

---

#### 🤖 智能模板创建（preset create）

> [!IMPORTANT]
> **AI 驱动的模板生成器**  
> 通过 AI 分析图片自动生成绘图模板，无需手动编写复杂提示词。

<details open>
<summary><b>📚 使用流程</b></summary>

1. **上传图片**  
   发送 `绘图模板 create` + 附带图片（或引用包含图片的消息）

2. **AI 分析**  
   AI 自动识别风格、元素、特征，生成模板名称 + 详细提示词

3. **交互会话**  
   - ✅ 回复 `确认` / `yes` 保存模板
   - ❌ 回复 `取消` / `no` 放弃操作
   - 📝 直接发送修改指令，AI 继续优化

</details>

**📸 实际效果展示**

<div align="center">

![preset create 示例](assets/image%20-%205.png)

*上传"监狱风格照"图片，AI 自动生成包含场景、光影、构图要求等专业提示词*

</div>

---

#### ✨ 智能模板优化（preset optimize）

> [!IMPORTANT]
> **AI 驱动的模板迭代器**  
> 对现有模板进行智能优化，支持交互式修改，直到达到理想效果。

<details open>
<summary><b>📚 使用流程</b></summary>

1. **选择模板**  
   发送 `绘图模板 optimize <模板名称>`

2. **提供指令**（可选）  
   添加具体优化要求，如：`白色标牌上写有文字`

3. **交互会话**  
   - ✅ 回复 `确认` 更新模板
   - ❌ 回复 `取消` 保持原样
   - 🔄 继续发送新指令进行多轮优化

</details>

**📸 实际效果展示**

<div align="center">

![preset optimize 示例](assets/image%20-%206.png)

*对"监狱风格照"模板添加"白色标牌上写有文字"指令，AI 智能融合生成更精准的提示词*

</div>

---

### 💡 使用示例

```bash
# 基础文生图
draw 一只可爱的猫

# 切换 API 引擎
draw 一只可爱的猫 -e api

# 使用模板 + 用户头像
draw -t 手办 @用户

# 模板 + 额外描述 + 多用户
draw -t 巨物手办 场景是夜晚 @用户1 @用户2

# 图生图 + 提示词优化
draw [附带图片] -o on 换成赛博朋克风格

# 引用图片 + 模板
[引用图片] draw -t 油画 添加温暖滤镜
```

---

## 🖼️ 生成示例

![示例一](assets/image%20-%201.png)
![示例二](assets/image%20-%202.png)
![示例三](assets/image%20-%203.png)
![示例四](assets/image%20-%204.png)

---

## ⚠️ 常见错误

<table>
<thead>
<tr>
<th>🚫 错误信息</th>
<th>🔍 可能原因</th>
<th>🛠️ 解决方案</th>
</tr>
</thead>
<tbody>
<tr>
<td><code>图片生成失败</code></td>
<td>
• 内容触发敏感词分类<br>
• 豆包页面人机验证<br>
• Cookie 已过期
</td>
<td>
• 修改描述，避免敏感词<br>
• 设置 <code>HEADLESS_BROWSER=False</code> 手动验证<br>
• 重新获取 Cookie
</td>
</tr>
<tr>
<td><code>队列超时</code></td>
<td>高并发任务堆积</td>
<td>稍后重试，或考虑切换到 API 引擎</td>
</tr>
<tr>
<td><code>API 调用失败</code></td>
<td>
• API Key 无效<br>
• 配额不足<br>
• 模型不支持图像生成
</td>
<td>
• 检查 API 配置<br>
• 确认账户余额<br>
• 更换支持图像的模型
</td>
</tr>
</tbody>
</table>

---

## ⚙️ 插件配置项

> [!NOTE]
> 所有配置可通过 `data/configs/config.yaml` 或 **WebUI** 调整。

<details>
<summary><b>🛠️ 点击展开完整配置说明</b></summary>

### 🎨 绘图引擎配置

| 配置项 | 默认值 | 说明 |
| :--- | :---: | :--- |
| `default_draw_engine` | `"doubao"` | 默认绘图引擎：`doubao` / `api` |
| `enable_api_draw_engine` | `True` | 是否允许非管理员使用 API 引擎 |
| `api_draw_model` | `"Gemini/gemini-2.5-flash-image-preview"` | API 引擎使用的模型 |

### ✨ 提示词优化配置

| 配置项 | 默认值 | 说明 |
| :--- | :---: | :--- |
| `enable_draw_prompt_optimization` | `False` | 是否默认开启提示词优化（`-o` 可覆盖） |
| `auxiliary_llm_model` | `"Gemini/gemini-2.5-flash"` | 提示词优化所用的 LLM |

### 🍪 豆包引擎配置

| 配置项 | 默认值 | 说明 |
| :--- | :---: | :--- |
| `DOUBAO_COOKIES` | `[]` | 豆包 Cookie 列表，支持多账号轮询 |
| `ENABLE_DOUBAO_COOKIES` | `True` | 是否启用 Cookie（强烈建议开启） |
| `HEADLESS_BROWSER` | `True` | 是否使用无头模式（调试时设为 `False`） |
| `browser_cooldown_seconds` | `60` | 浏览器关闭后的冷却时长（秒） |

### ⏱️ 限流控制配置

| 配置项 | 默认值 | 说明 |
| :--- | :---: | :--- |
| `draw_cd` | `120` | 绘图命令冷却时间（秒） |

</details>

