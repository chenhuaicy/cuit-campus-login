# 成信大校园网助手（Windows 试用版）

面向成都信息工程大学航空港校区的个人校园网登录工具，非学校官方软件。
当前适配 `10.254.241.66` 的网页登录、运营商选择和确认流程。

## 使用

从 [Releases 下载分享包](https://github.com/chenhuaicy/cuit-campus-login/releases)，解压后运行 `CUIT-AutoLogin.exe`。
需要 Windows 10/11 64 位及已安装的 Microsoft Edge；EXE 版本无需安装 Python。

1. 连接校园 Wi-Fi 或校园网网线。
2. 填写自己的校园网账号密码，选择与套餐一致的运营商。
3. 阅读学校认证页协议后勾选同意，点击“保存并开始”。
4. 需要自动登录时，勾选记住密码、Windows 登录后自动运行和掉线自动重连并保存。

支持电信、移动、联通、校园网选项。已在一名航空港用户的电信账号上确认可登录；
其他运营商目前仅经过模拟页面测试，不能保证所有校区、套餐均适配。

## 功能

- 完成网页登录、选择运营商并点击“确定”。
- Windows 用户登录后自动开始监测，约每 30 秒检查一次。
- 单轮联网检查失败后，间隔 5 秒复查两轮，连续三轮失败才尝试认证。
- 登录期间检测到联网后，关闭本工具创建的临时浏览器；手动关窗不停止监测。
- 最小化到通知区域，托盘菜单支持恢复窗口和退出。
- 可选择点击 × 时收进托盘或直接退出。
- 联网后尝试关闭已识别的系统认证标签页，保留 Edge 里的其他网页。

## 账号与本机设置

分享包和源码不包含账号、密码、个人截图或本机配置。
配置保存在 `%LOCALAPPDATA%\CUIT-AutoLogin\settings.json`。
账号和偏好为普通文本；勾选记住密码时，密码由 Windows DPAPI 按当前用户加密。
DPAPI 不保护已被他人控制的 Windows 登录会话。运行时凭据会交给学校认证页面。
当前校园网入口是 HTTP，程序不改变学校的传输方式。

仅对固定校园网主机填写凭据。程序不把凭据上传到开发者服务器。
开机启动只写入当前 Windows 用户的 Run 项。点击“清除保存信息”可清除配置及该启动项。

## 已知限制

- 本程序通过 Playwright 调用系统 Edge，包较大，登录时会启动浏览器进程。
- 网络检测依赖两个 Microsoft 检测地址。检测服务被阻断、VPN 和网络策略可能导致误判。
  连续复查能减少短暂波动造成的误弹窗，不能证明每次检测失败都代表校园网下线。
- 实际认证流程、页面结构、按钮文字、验证码或学校策略变化可能需要更新适配。
- 清理系统弹窗依赖 Edge 的 Windows UI Automation。只核对当前选中的认证标签页，
  不自动遍历或切换用户的其他网页。若已经切到其他标签页，会跳过关闭。
- 这是早期试用版，没有代码签名，尚未经过大规模设备、长时间运行及不同运营商验证。

## 从源码运行及打包

在 Windows 上安装 Python 3.12（包含 Tkinter），在本目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe campus_login.py
```

打包：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name CUIT-AutoLogin --hidden-import pystray._win32 --icon campus-icon.ico --add-data "campus-icon.ico;." --add-data "close_portal_tab.ps1;." campus_login.py
```

无需运行 `playwright install`，程序使用本机 Edge。
发布时上传源码至仓库，把 EXE/分享 ZIP 附加到 Releases；不要上传 `settings.json`、日志、个人截图或构建缓存。

## 图标与依赖

图标通过 AI 图像工具生成，表现书页、双翼和网络信号，不是学校官方校徽。
运行依赖 Playwright、pystray、Pillow；打包工具为 PyInstaller。各依赖按其自身许可证使用。

## 反馈问题

反馈时说明校区、运营商、Windows 版本以及停在哪一步。截图请遮住账号、密码和会话参数。
请勿在 Issue、提交或截图中公开个人配置文件。公开源码便于审阅；第三方依赖的许可证仍分别适用。
