# HRMS 中国化定制说明

本 HRMS 应用已针对中国市场进行定制，删除了不适用的印度税务功能。

## 定制内容

### 删除的印度税务 DocType
- Employee Other Income（员工其他收入）
- Employee Tax Exemption Category（员工税务豁免类别）
- Employee Tax Exemption Declaration（员工税务豁免申报）
- Employee Tax Exemption Declaration Category（员工税务豁免申报类别）
- Employee Tax Exemption Proof Submission（员工税务豁免证明提交）
- Employee Tax Exemption Proof Submission Detail（员工税务豁免证明提交明细）
- Employee Tax Exemption Sub Category（员工税务豁免子类别）
- Income Tax Slab（所得税税率表）
- Gratuity（养老金）
- Gratuity Applicable Component（养老金适用组件）
- Gratuity Rule（养老金规则）
- Gratuity Rule Slab（养老金规则档次）

### 删除的报表
- Professional Tax Deductions（专业税扣除）
- Provident Fund Deductions（公积金扣除）
- Income Tax Computation（所得税计算）
- Income Tax Deductions（所得税扣除）

### 清理的配置
- Workspace 配置（Tax & Benefits, Payroll）
- Workspace Sidebar 配置
- DocType 字段（salary_structure_assignment 和 bulk_salary_structure_assignment 中的 income_tax_slab 字段）
- Number Card（Total Declaration Submitted）

### 汉化内容
- Dashboard Chart（仪表板图表）
- Number Card（数字卡片）
- 考勤相关模块

## 分支管理

### 当前分支结构
- `china-customization` - 中国化定制分支（当前分支）
- `version-16` - 官方 version-16 分支的本地副本

### 版本标签
- `china-v1.0` - 中国化定制版本 1.0

## 更新策略

### 从官方更新时的步骤

1. **查看官方更新内容**
   ```bash
   git fetch upstream
   git log china-customization..upstream/version-16 --oneline
   ```

2. **使用 rebase 合并更新**
   ```bash
   # 确保在 china-customization 分支
   git checkout china-customization
   
   # 使用 rebase 合并官方更新
   git rebase upstream/version-16
   ```

3. **解决冲突**
   如果出现冲突：
   ```bash
   # 查看冲突文件
   git status
   
   # 手动编辑冲突文件，保留中国化定制
   # 编辑完成后
   git add <冲突文件>
   git rebase --continue
   ```

4. **如果遇到问题，可以中止 rebase**
   ```bash
   git rebase --abort
   ```

5. **验证更新后的系统**
   ```bash
   bench clear-cache
   bench --site aierp.local migrate
   ```

### 选择性合并（推荐）

如果只想合并特定的功能：

```bash
# 查看官方的某个提交
git show <commit-hash>

# 选择性合并某个提交
git cherry-pick <commit-hash>
```

### 回滚到稳定版本

如果更新出现问题：

```bash
# 回滚到标签版本
git reset --hard china-v1.0

# 或回滚到特定提交
git reset --hard <commit-hash>
```

## 注意事项

1. **不要直接在 version-16 分支上工作**
   - 始终在 `china-customization` 分支上进行修改
   - version-16 分支保持与官方同步

2. **更新前备份数据库**
   ```bash
   bench --site aierp.local backup
   ```

3. **避免使用 bench update**
   - `bench update` 会强制更新所有应用
   - 建议手动控制 HRMS 的更新

4. **定期创建标签**
   每次重要修改后创建标签：
   ```bash
   git tag -a china-v1.1 -m "描述"
   ```

5. **记录删除的文件**
   如果官方更新恢复了印度税务功能，参考本文档重新删除

## 维护建议

### 定期同步官方更新
建议每季度检查一次官方更新：
```bash
git fetch upstream
git log china-customization..upstream/version-16 --oneline --graph
```

### 只合并必要的更新
- 安全补丁：立即合并
- Bug 修复：评估后合并
- 新功能：根据需求选择性合并
- 印度特定功能：忽略

### 文档化每次修改
每次修改后更新此文档，记录：
- 修改内容
- 修改原因
- 影响范围

## 联系信息

- 定制版本维护者：借书人
- 创建日期：2026-04-20
- 最后更新：2026-04-20

## 相关文档

- [CLAUDE.md](../../CLAUDE.md) - 项目整体说明
- [官方 HRMS 文档](https://github.com/frappe/hrms)
