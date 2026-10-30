# 纯净源码发布说明

本仓库为 `Winter0723/ReID` 的独立源码发行仓库，默认分支为 `main`。它包含后端、运营工作台、游客 H5、模型适配代码、配置模板、测试和部署文档；开发仓库的 Git 历史、真实配置、数据库、运行媒体及模型权重不随源码导入。

## 获取与更新

首次使用从[主页教程](../README.md)开始：

```sh
git clone https://github.com/Winter0723/ReID.git
cd ReID
```

已有部署先按[备份恢复](BACKUP_RESTORE.md)备份，再用 `git pull --ff-only origin main` 更新。保留已有私有配置和模型，按原 Compose 项目名重建服务。模型下载入口见[模型说明](../model/README.md)，赞助入口见[支持项目](../README.md#support)。

## 发行边界

- 源码采用 AGPL-3.0-only；第三方说明见根目录 LICENSE、NOTICE.md 与模型/素材说明。
- 模型权重通过网盘另行获取，下载后按清单校验。网盘分享不改变上游许可。
- 真实 `.env`、密码、证书、数据库、游客素材、日志和备份不得加入仓库。
- 赞助收款码由维护者提供，位于 `docs/assets/sponsorship/`，仅用于自愿支持项目。
- 从开发仓库同步新版本时，只复制审核后的源码文件，保留本仓库独立历史；不得复制开发仓库 `.git` 或执行镜像推送。同步后保留本仓库的下载地址、`main` 命令及赞助材料。

## 维护者发布检查

```sh
python scripts/check_public_config.py
python scripts/export_public_source.py --check
python -m unittest discover -s scripts/tests -p test_public_release.py -v
python -m unittest discover -s scripts/tests -p test_deployment.py -v
python -m unittest discover -s scripts/tests -p test_compose_contract.py -v
```

`SOURCE_MANIFEST.json` 记录发布时除其自身外各源码文件的 SHA256。修改发布内容后必须重新生成清单；归档工具在导出 ZIP 时会重新计算清单，输出路径必须不存在：

```sh
python scripts/export_public_source.py --output release/reid-source.zip
```

发布前还应检查当前提交的 CI、安全扫描、模型获取及目标机器业务流程。配置检查通过不能替代实际部署、SMTP 投递、相机采集、检索、完整出片及备份恢复验收。历史实测只代表标注日期和环境。

## 当前运行范围

本机体验使用开发验证码与测试游客入口；正式环境使用真实 SMTP、独立管理员邮箱与 HTTPS。游客可浏览器登记，配置好公众号后也可微信登录；当前完整生产预检要求微信配置，详见[部署说明](NEW_MACHINE_DEPLOYMENT.md)。收款采用工作人员人工确认，真实微信自动支付尚未完成接入验收。
