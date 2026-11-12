import { useCallback, useEffect, useMemo, useState } from 'react'

import { request, readErrorDetail } from '@/api/client'
import { AUDIT_DEPARTMENTS, AUDIT_ROLES, DEFAULT_DEPARTMENT, DEFAULT_ROLE, auditHeaders } from '@/api/audit'

const ENDPOINT = '/api/query'
const columns = [
  "模板编号",
  "模板名称",
  "查询语句",
  "参数定义",
  "缓存时间",
  "结果上限",
  "执行超时",
  "模板版本"
]
const actions = [
  "提交审批",
  "发布模板",
  "停用模板"
]
const statuses = [
  "草稿",
  "待审批",
  "已发布",
  "已停用"
]
const stats = [
  {
    "label": "查询模板总数",
    "value": 0
  },
  {
    "label": "已发布模板",
    "value": 0
  },
  {
    "label": "待审批模板",
    "value": 0
  }
]

type Row = Record<string, string | number | null> & {
  访问模式?: 'owner' | 'readonly' | 'denied'
  访问说明?: string
  脱敏字段?: string[]
}

const MODE_BADGE: Record<string, string> = {
  owner: 'badge-owner',
  readonly: 'badge-readonly',
  denied: 'badge-denied',
}

export default function QueryView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [department, setDepartment] = useState(DEFAULT_DEPARTMENT)
  const [role, setRole] = useState(DEFAULT_ROLE)
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  // 与审计工作区共用同一套部门/角色请求头，后端返回的脱敏结果因此完全一致。
  const auditHeadersValue = useMemo(() => auditHeaders(department, role), [department, role])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`, { headers: auditHeadersValue })
      if (!response.ok) {
        throw new Error(await readErrorDetail(response, '查询模板列表读取失败'))
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setRows([])
      setTotal(0)
      setErrorMessage(error instanceof Error ? error.message : '聚合查询配置列表读取失败')
    }
  }, [filters, auditHeadersValue])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        headers: auditHeadersValue,
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error(await readErrorDetail(response, '聚合查询配置动作未生效'))
      }
      const payload = await response.json()
      if (payload.ok === false) {
        throw new Error(payload.message || '聚合查询配置动作未生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '聚合查询配置操作失败')
    }
  }

  return (
    <section className="page" data-module="query">
      <header className="page-head">
        <div>
          <h2>聚合查询配置管理</h2>
          <p className="page-desc">维护预定义的聚合查询模板、参数化查询与缓存策略，提升分析效率。</p>
        </div>
        <div className="page-actions">
          <label className="filter-item">
            <span>审计部门</span>
            <select value={department} onChange={(event) => setDepartment(event.target.value)}>
              {AUDIT_DEPARTMENTS.map((item) => (
                <option key={item.code} value={item.code}>{item.label}</option>
              ))}
            </select>
          </label>
          <label className="filter-item">
            <span>审计角色</span>
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {AUDIT_ROLES.map((item) => (
                <option key={item.code} value={item.code}>{item.label}</option>
              ))}
            </select>
          </label>
          <button className="btn primary" type="button" onClick={() => setErrorMessage('查询模板登记入口尚未接入审批流')}>
            登记查询模板
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出聚合查询配置清单
          </button>
          <span className="audit-note">
            清单与审计工作区共用同一脱敏结果：切换部门后敏感字段同步隐藏，共享只读副本看不到原始参数。
          </span>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={row.访问模式 === 'readonly' ? 'row-readonly' : ''}>
              {columns.map((column) => (
                <td key={column}>
                  {row[column] ?? '—'}
                  {Array.isArray(row.脱敏字段) && row.脱敏字段.includes(column) && (
                    <div className="masked-hint">已脱敏</div>
                  )}
                </td>
              ))}
              <td className="row-actions">
                <span className={`badge ${MODE_BADGE[row.访问模式 ?? 'owner']}`}>{row.访问模式 ?? 'owner'}</span>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无聚合查询配置数据，可先登记查询模板</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条聚合查询配置记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
