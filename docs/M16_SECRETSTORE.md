# M16 SecretStore 与运维凭据边界（M16-18）

## 存储

- 密文根由部署端指定，必须在业务 `data/`、静态目录、发布包和业务备份之外；本仓库默认不创建固定 secret 目录。
- 每个 secret 一个 `.enc` 文件：magic + JSON header（scope/generation/created_at/size/ciphertext_sha256）+ 认证密文。
- POSIX 权限 0600；原子写（临时文件 + fsync + replace）；删除失败显式报错，不谎称成功。
- AAD 绑定 `workspace_id + connection_id + generation`；生产 Fernet 把 AAD 放进认证明文内；header scope 再次复核。
- 密钥由部署端注入（`MCHECKER_SECRET_KEY`，base64url 或 hex，≥32 字节）；密钥与密文分离，无密钥时 `key_missing`，**没有明文回退**。

## 管理员授权（2026-11-06 用户确认）

- 全系统只有管理员和访客；`integration_credentials_manage` 映射管理员。
- 连接写入、凭据导入/验证/激活/撤销、保活配置和立即执行仅管理员可用；访客可查看安全投影。
- 不再要求 `MCHECKER_OPERATOR_TOKEN` 或 `X-Operator-Token`，旧头不会给访客授权。
- 登记姓名是身份信息，不是额外的成员角色；撤权在下一次请求生效。
- 此变更只统一授权方式，SecretStore 加密、密钥注入、凭据生命周期、CAS、工作区隔离与脱敏不变。

## 测试边界

- `tests/test_secret_store.py` 使用测试专用 Cipher 验证 scope/AAD/原子写/篡改/删除语义；生产默认 `FernetCipher` 需要 collector 环境的 `cryptography`。
- 真实部署密钥注入、轮换、撤销重启不复活仍属 E4/E5 外部证据；未取得前不宣称生产可用。
