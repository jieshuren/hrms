# HRMS 更新快速指南

## 当前状态
- **当前分支**: `china-customization`
- **基于版本**: Frappe HRMS v16.5.0
- **定制标签**: `china-v1.0`
- **远程仓库**: `upstream` → https://github.com/frappe/hrms.git

## 快速命令

### 查看可用更新
```bash
git fetch upstream
git log china-customization..upstream/version-16 --oneline
```

### 更新前准备
```bash
# 1. 备份数据库
bench --site aierp.local backup

# 2. 确认当前分支
git branch
# 应该显示 * china-customization

# 3. 查看当前修改
git status
```

### 执行更新（推荐方式）
```bash
# 方式一：使用 rebase（保持提交历史清晰）
git fetch upstream
git rebase upstream/version-16

# 如果有冲突，解决后：
git add <冲突文件>
git rebase --continue

# 如果想放弃 rebase：
git rebase --abort
```

### 选择性合并特定功能
```bash
# 1. 查看官方某个提交的详情
git show <commit-hash>

# 2. 只合并这个提交
git cherry-pick <commit-hash>

# 3. 如果有冲突，解决后：
git add <冲突文件>
git cherry-pick --continue
```

### 更新后验证
```bash
# 1. 清空缓存
bench clear-cache

# 2. 运行迁移
bench --site aierp.local migrate

# 3. 验证印度税务功能是否被清理
bench console
```

在控制台中运行：
```python
import frappe
# 检查印度税务 DocType 是否存在
india_doctypes = ['Income Tax Slab', 'Employee Tax Exemption Declaration']
for dt in india_doctypes:
    exists = frappe.db.exists('DocType', dt)
    print(f"{dt}: {'存在 ✗' if exists else '已删除 ✓'}")
exit()
```

### 回滚到稳定版本
```bash
# 回滚到标签版本
git reset --hard china-v1.0

# 清空缓存并重新迁移
bench clear-cache
bench --site aierp.local migrate
```

## 常见问题

### Q: 更新后印度税务功能又出现了怎么办？
A: 参考 `CHINA_CUSTOMIZATION.md` 文档，重新执行清理步骤，或者回滚到 `china-v1.0` 标签。

### Q: 合并冲突太多怎么办？
A: 
1. 先 `git rebase --abort` 放弃本次更新
2. 使用 `git log upstream/version-16 --oneline` 查看官方更新
3. 只 cherry-pick 你需要的提交

### Q: 如何知道哪些更新值得合并？
A: 
- 安全补丁：立即合并
- Bug 修复：评估影响后合并
- 新功能：根据需求决定
- 印度/阿联酋特定功能：忽略

### Q: bench update 会影响我的定制吗？
A: 
`bench update` 会尝试更新所有应用。建议：
1. 不要直接运行 `bench update`
2. 手动控制 HRMS 的更新
3. 其他应用可以正常更新

## 紧急恢复

如果更新导致系统无法使用：

```bash
# 1. 回滚代码
git reset --hard china-v1.0

# 2. 恢复数据库（如果需要）
bench --site aierp.local restore <备份文件路径>

# 3. 清空缓存
bench clear-cache

# 4. 重启服务
bench restart
```

## 创建新标签

每次重要修改后创建标签：

```bash
git tag -a china-v1.1 -m "版本 1.1 - 描述修改内容"
git tag  # 查看所有标签
```

## 推送到远程（如果有自己的 fork）

```bash
# 推送分支
git push origin china-customization

# 推送标签
git push origin china-v1.0
```

## 更多信息

详细说明请参考：
- [CHINA_CUSTOMIZATION.md](./CHINA_CUSTOMIZATION.md) - 完整的定制说明
- [../../CLAUDE.md](../../CLAUDE.md) - 项目整体文档
