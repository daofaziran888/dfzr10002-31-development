import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { request, readErrorDetail } from '@/api/client'
import { AUDIT_DEPARTMENTS, AUDIT_ROLES, DEFAULT_DEPARTMENT, DEFAULT_ROLE, auditHeaders } from '@/api/audit'

const ENDPOINT = '/api/log'

type AccessMode = 'owner' | 'readonly' | 'denied'

type AuditAction = {
  id: number
  log_no: string
  action_type: string
  action_label: string
  operator: string
  operation_time: string
  duration: string
  rows_returned: string
  remark: string
  sensitive_params: string
  masked_fields: string[]
  capture_status: string
  capture_note: string
  capture_ok: boolean
  status: string
  object_type: string
  object_code: string
  object_name?: string
}

type MissingRange = {
  object_code: string
  object_type: string
  from: string
  to: string
  reason: string
  status: string
}

type Chain = {
  object_code: string
  object_type: string
  object_name: string
  access_mode: AccessMode
  access_label: string
  actions: AuditAction[]
  action_count: number
  missing: MissingRange[]
}

type Workspace = {
  object: { object_code: string; object_type: string; object_name: string; department: string; department_label: string }
  access: { mode: AccessMode; label: string; department: string; department_label: string; role: string; role_label: string }
  filters: {
    operator: string
    action_type: string
    date_from: string
    date_to: string
    operator_options: string[]
    action_type_options: string[]
  }
  summary: {
    action_count: number
    object_count: number
    missing_count: number
    operator_count: number
    time_range: { start: string; end: string }
  }
  links: { object_code: string; object_type: string; object_name: string; relation: string; access_mode: AccessMode }[]
  chains: Chain[]
  timeline: AuditAction[]
  missing: MissingRange[]
  capture_breaks: AuditAction[]
}

type ObjectOption = { object_code: string; object_type: string; object_name: string; actions: number; missing: number }

const MODE_BADGE: Record<AccessMode, string> = {
  owner: 'badge-owner',
  readonly: 'badge-readonly',
  denied: 'badge-denied',
}

export default function LogAuditView() {
  const [searchParams] = useSearchParams()
  const [department, setDepartment] = useState(DEFAULT_DEPARTMENT)
  const [role, setRole] = useState(DEFAULT_ROLE)
  const [objectOptions, setObjectOptions] = useState<ObjectOption[]>([])
  const [objectCode, setObjectCode] = useState(() => searchParams.get('object') || 'INDI-0002')
  const [operator, setOperator] = useState('')
  const [actionType, setActionType] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [denied, setDenied] = useState(false)
  const [loading, setLoading] = useState(false)

  const headers = useMemo(() => auditHeaders(department, role), [department, role])

  // 对象清单只展示当前部门可见的对象；切换部门时同步刷新。
  useEffect(() => {
    let cancelled = false
    request(`${ENDPOINT}/objects`, { headers })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(await readErrorDetail(response, '可审计对象清单读取失败'))
        }
        const payload = (await response.json()) as { items: ObjectOption[] }
        if (!cancelled) {
          setObjectOptions(payload.items ?? [])
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setObjectOptions([])
          setErrorMessage(error instanceof Error ? error.message : '可审计对象清单读取失败')
        }
      })
    return () => {
      cancelled = true
    }
  }, [headers])

  const reload = useCallback(async () => {
    if (!objectCode.trim()) {
      setWorkspace(null)
      return
    }
    setLoading(true)
    setErrorMessage('')
    setDenied(false)
    // 过滤参数传了才带上，避免空串把后端筛选误判成“按空值匹配”。
    const query = new URLSearchParams()
    if (operator.trim()) query.set('operator', operator.trim())
    if (actionType.trim()) query.set('action_type', actionType.trim())
    if (dateFrom) query.set('date_from', dateFrom)
    if (dateTo) query.set('date_to', dateTo)
    try {
      const response = await request(`${ENDPOINT}/workspace/${encodeURIComponent(objectCode.trim())}?${query}`, { headers })
      if (!response.ok) {
        // 越权 403 与未知对象 404 都在这里显式展示原因，不清空筛选条件假装成功。
        setDenied(response.status === 403)
        throw new Error(await readErrorDetail(response, '审计链路读取失败'))
      }
      setWorkspace((await response.json()) as Workspace)
    } catch (error) {
      setWorkspace(null)
      setErrorMessage(error instanceof Error ? error.message : '审计链路读取失败')
    } finally {
      setLoading(false)
    }
  }, [objectCode, operator, actionType, dateFrom, dateTo, headers])

  useEffect(() => {
    void reload()
  }, [reload])

  return (
    <section className="page" data-module="log-audit">      <header className="page-head">
        <div>
          <h2>操作日志审计工作区</h2>
          <p className="page-desc">按操作对象串联同一业务对象在指标口径、查询模板与数据服务上的全部动作；敏感参数统一脱敏，越权查询直接拒绝。</p>
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
        </div>
      </header>

      {workspace && (
        <div className={`audit-scope ${MODE_BADGE[workspace.access.mode]}`}>
          <strong>{workspace.access.department_label} · {workspace.access.role_label}</strong>
          <span>{workspace.access.label}</span>
        </div>
      )}

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>操作对象</span>
          <select value={objectCode} onChange={(event) => setObjectCode(event.target.value)}>
            {!objectOptions.some((item) => item.object_code === objectCode) && objectCode && (
              <option value={objectCode}>{objectCode}（当前部门不可见）</option>
            )}
            {objectOptions.map((item) => (
              <option key={item.object_code} value={item.object_code}>
                {item.object_code} · {item.object_name}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-item">
          <span>操作人</span>
          <input
            list="audit-operator-options"
            value={operator}
            placeholder="按操作人筛选"
            onChange={(event) => setOperator(event.target.value)}
          />
          <datalist id="audit-operator-options">
            {(workspace?.filters.operator_options ?? []).map((item) => <option key={item} value={item} />)}
          </datalist>
        </label>
        <label className="filter-item">
          <span>动作类型</span>
          <input
            list="audit-action-options"
            value={actionType}
            placeholder="按动作类型筛选"
            onChange={(event) => setActionType(event.target.value)}
          />
          <datalist id="audit-action-options">
            {(workspace?.filters.action_type_options ?? []).map((item) => <option key={item} value={item} />)}
          </datalist>
        </label>
        <label className="filter-item">
          <span>开始时间</span>
          <input type="datetime-local" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>结束时间</span>
          <input type="datetime-local" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
        </label>
        <button className="btn primary" type="submit" disabled={loading}>{loading ? '读取中…' : '查询审计链路'}</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => { setOperator(''); setActionType(''); setDateFrom(''); setDateTo('') }}
        >
          重置筛选
        </button>
      </form>

      {errorMessage && (
        <div className={`audit-error ${denied ? 'audit-error-denied' : ''}`} role="alert">
          <strong>查询未完成：</strong>{errorMessage}
          <span className="audit-error-note">本次越权或失败的查询没有写入任何操作日志，也不会生成审计结论。</span>
        </div>
      )}
      {!errorMessage && !workspace && (
        <div className="audit-empty">暂无审计链路数据，请选择操作对象后查询</div>
      )}

      {workspace && (
        <>
          <div className="audit-object-head">
            <div>
              <h3>{workspace.object.object_code} · {workspace.object.object_name}</h3>
              <span className="audit-muted">{workspace.object.object_type} / 归属部门：{workspace.object.department_label || workspace.object.department}</span>
            </div>
            <div className="stat-row audit-stat-row">
              <article className="stat-card">
                <span className="stat-label">链路动作</span>
                <strong className="stat-value">{workspace.summary.action_count}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">串联对象</span>
                <strong className="stat-value">{workspace.summary.object_count}</strong>
              </article>
              <article className="stat-card">
                <span className="stat-label">缺失 / 异常</span>
                <strong className={`stat-value ${workspace.summary.missing_count ? 'stat-warn' : ''}`}>
                  {workspace.summary.missing_count}
                </strong>
              </article>
            </div>
          </div>

          <div className="audit-links">
            {workspace.links.map((link) => (
              <span key={link.object_code} className="audit-link-chip">
                <b>{link.relation}</b>
                <span>{link.object_code}</span>
                <span>{link.object_type}</span>
                <span className={`badge ${MODE_BADGE[link.access_mode]}`}>{link.access_mode}</span>
              </span>
            ))}
          </div>

          <div className="audit-chain-block">
            {workspace.chains.map((chain) => (
              <article key={chain.object_code} className="chain-card">
                <header className="chain-head">
                  <div>
                    <strong>{chain.object_code}</strong>
                    <span className="audit-muted"> {chain.object_type} · {chain.object_name}</span>
                  </div>
                  <span className={`badge ${MODE_BADGE[chain.access_mode]}`}>{chain.action_count} 个动作</span>
                  {chain.missing.length > 0 && (
                    <span className="badge badge-warn">{chain.missing.length} 段日志缺失</span>
                  )}
                </header>
                {chain.actions.length === 0 && (
                  <div className="audit-empty">该对象在当前筛选条件下没有动作记录</div>
                )}
                {chain.actions.map((action) => (
                  <div key={`${chain.object_code}-${action.id}`} className="action-row">
                    <span className="action-time">{action.operation_time}</span>
                    <span className="action-type">{action.action_type}</span>
                    <span className="action-operator">{action.operator}</span>
                    {!action.capture_ok && <span className="badge badge-warn">{action.capture_status}</span>}
                    <span className={`badge ${action.status === '已归档' ? 'badge-muted' : 'badge-ok'}`}>{action.status}</span>
                    {action.masked_fields.length > 0 && <span className="badge badge-mask">已脱敏</span>}
                  </div>
                ))}
              </article>
            ))}
          </div>

          <h3 className="audit-section-title">串联时间线（按操作时间倒序）</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>操作时间</th>
                <th>操作对象</th>
                <th>动作类型</th>
                <th>操作人</th>
                <th>查询耗时</th>
                <th>返回行数</th>
                <th>敏感参数</th>
                <th>日志备注</th>
                <th>采集状态</th>
                <th>日志状态</th>
              </tr>
            </thead>
            <tbody>
              {workspace.timeline.map((action) => (
                <tr key={action.id} className={action.capture_ok ? '' : 'row-warn'}>
                  <td>{action.operation_time}</td>
                  <td>{action.object_code} · {action.object_name ?? action.object_type}</td>
                  <td>{action.action_type}</td>
                  <td>{action.operator}</td>
                  <td>{action.duration}</td>
                  <td>{action.rows_returned}</td>
                  <td className="masked-cell">
                    {action.sensitive_params || '—'}
                    {action.masked_fields.length > 0 && <div className="masked-hint">脱敏字段：{action.masked_fields.join('、')}</div>}
                  </td>
                  <td>{action.remark || '—'}</td>
                  <td>
                    {action.capture_ok
                      ? '正常'
                      : <span className="badge badge-warn">{action.capture_status}</span>}
                    {!action.capture_ok && action.capture_note && <div className="masked-hint">{action.capture_note}</div>}
                  </td>
                  <td>{action.status}</td>
                </tr>
              ))}
              {workspace.timeline.length === 0 && (
                <tr>
                  <td className="empty-state" colSpan={10}>当前筛选条件下没有可串联的动作记录</td>
                </tr>
              )}
            </tbody>
          </table>

          {workspace.missing.length > 0 && (
            <>
              <h3 className="audit-section-title">日志缺失标记（不计入动作记录）</h3>
              <div className="missing-list">
                {workspace.missing.map((item) => (
                  <MissingNotice key={`${item.object_code}-${item.from}`} item={item} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  )
}

function MissingNotice({ item }: { item: MissingRange }) {
  return (
    <div className="missing-notice" role="note">
      <span className="badge badge-warn">{item.status}</span>
      <b>{item.object_code}</b>
      <span>{item.object_type}</span>
      <span>缺失区间 {item.from} ~ {item.to}</span>
      <span className="audit-muted">{item.reason}</span>
    </div>
  )
}
