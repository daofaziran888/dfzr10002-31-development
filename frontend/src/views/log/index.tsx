import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { request, readErrorDetail } from '@/api/client'

const ENDPOINT = '/api/log'
const columns = [
  "日志编号",
  "操作类型",
  "操作对象",
  "操作人",
  "操作时间",
  "查询耗时",
  "返回行数",
  "日志备注"
]
const actions = [
  "标记复核",
  "归档记录"
]
const statuses = [
  "已记录",
  "已复核",
  "已归档"
]

type Row = Record<string, string | number | null> & {
  采集标记?: { status: string; reason: string; from: string; to: string }
  访问说明?: string
  脱敏字段?: string[]
}

// 列表筛选项与后端查询参数一一对应，避免把中文列名当成参数名发出去。
const FILTER_FIELDS = [
  { key: 'keyword', label: '日志编号' },
  { key: 'object_code', label: '操作对象' },
  { key: 'operator', label: '操作人' },
  { key: 'action_type', label: '操作类型' },
]

export default function LogView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    for (const field of FILTER_FIELDS) {
      const value = (filters[field.key] ?? '').trim()
      if (value) query.set(field.key, value)
    }
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error(await readErrorDetail(response, '操作日志列表读取失败'))
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析操作日志列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        // 权限不足等拒绝原因原样展示；拒绝路径不会改动日志记录。
        throw new Error(await readErrorDetail(response, '分析操作日志动作未生效'))
      }
      const payload = await response.json()
      if (payload.ok === false) {
        throw new Error(payload.message || '分析操作日志动作未生效')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '分析操作日志操作失败')
    }
  }

  const totalCount = rows.length
  const reviewedCount = rows.filter((row) => row.status === '已复核').length
  const archivedCount = rows.filter((row) => row.status === '已归档').length

  return (
    <section className="page" data-module="log">
      <header className="page-head">
        <div>
          <h2>分析操作日志管理</h2>
          <p className="page-desc">维护分析操作的全量日志记录，支持性能分析与合规审计。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('操作日志登记入口尚未接入审批流')}>
            登记操作日志
          </button>
          <Link className="btn" to="/log/audit">进入审计工作区</Link>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出分析操作日志清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {[
          { label: '操作日志总数', value: totalCount },
          { label: '已复核记录', value: reviewedCount },
          { label: '已归档记录', value: archivedCount },
        ].map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {FILTER_FIELDS.map((field) => (
          <label key={field.key} className="filter-item">
            <span>{field.label}</span>
            <input
              value={filters[field.key] ?? ''}
              placeholder={`按${field.label}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field.key]: event.target.value }))}
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
            <tr key={String(row.id)} className={row.采集标记 ? 'row-warn' : ''}>
              {columns.map((column) => (
                <td key={column}>
                  {row[column] ?? '—'}
                  {column === '操作对象' && (
                    <div className="masked-hint">
                      <Link to={`/log/audit?object=${encodeURIComponent(String(row['操作对象'] ?? ''))}`}>串联审计链路</Link>
                    </div>
                  )}
                  {column === '操作类型' && row.采集标记 && (
                    <div><span className="badge badge-warn">{row.采集标记.status}</span></div>
                  )}
                  {column === '日志备注' && Array.isArray(row.脱敏字段) && row.脱敏字段.length > 0 && (
                    <div className="masked-hint">已脱敏：{row.脱敏字段.join('、')}</div>
                  )}
                </td>
              ))}
              <td className="row-actions">
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无分析操作日志数据，可先登记操作日志</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条分析操作日志记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
