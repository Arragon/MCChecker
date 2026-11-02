# M16 SecretStore 与运维凭据边界（M16-18）

## 存储

- 密文根由部署端指定，必须在业务 `data/`、静态目录、发布包和业务备份之外；本仓库默认不创建固定 secret 目录。
- 每个 secret 一个 `.enc` 文件：magic + JSON header（scope/generation/created_at/size/ciphertext_sha256）+ 认证密文。
- POSIX 权限 0600；原子写（临时文件 + fsync + replace）；删除失败显式报错，不谎称成功。
- AAD 绑定 `workspace_id + connection_id + generation`；生产 Fernet 把 AAD 放进认证明文内；header scope 再次复核。
- 密钥由部署端注入（`MCHECKER_SECRET_KEY`，base64url 或 hex，≥32 字节）；密钥与密文分离，无密钥时 `key_missing`，**没有明文回退**。

## 运维授权

- `MCHECKER_OPERATOR_TOKEN` 是部署端高熵 token；`app/services/sync_operator_auth.py` 用恒定时间比较。
- 该能力独立于全局 admin/deployer，不新增空间角色/全站登录。
- token 不进入日志、错误正文、任务结果、Linear、Git 或备份。

## 测试边界

- `tests/test_secret_store.py` 使用测试专用 Cipher 验证 scope/AAD/原子写/篡改/删除语义；生产默认 `FernetCipher` 需要 collector 环境的 `cryptography`。
- 真实部署密钥注入、轮换、撤销重启不复活仍属 E4/E5 外部证据；未取得前不宣称生产可用。
