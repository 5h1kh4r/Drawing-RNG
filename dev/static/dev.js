const canvas = document.getElementById('verifyCanvas');
const drawing = setupCanvas(canvas, document.getElementById('verifyCount'));

let enrollments = [];
let selected = null;
let selectedVerifications = [];
let selectedVerification = null;

function $(id) { return document.getElementById(id); }
function shortId(value) { return String(value || '').slice(0, 8); }
function n3(value) { return Number(value || 0).toFixed(3); }
function attemptLabel(row) {
  return `${String(row.attempt_type || 'verification').replaceAll('_', ' ')} ${shortId(row.id)}`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || 'Request failed');
  return data;
}
function getJson(url) { return requestJson(url); }
function deleteJson(url) { return requestJson(url, { method: 'DELETE' }); }
function patchJson(url, body) {
  return requestJson(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
}

function jsonValue(value, fallback) {
  if (typeof value !== 'string') return value ?? fallback;
  try { return JSON.parse(value); } catch (_) { return fallback; }
}

function drawPreview(target, rawStrokes) {
  const strokes = jsonValue(rawStrokes, []);
  const ctx = target.getContext('2d');
  const width = target.width;
  const height = target.height;
  ctx.fillStyle = '#fbfdff';
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = '#e8eef6';
  ctx.lineWidth = 1;
  for (let x = 40; x < width; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
  }
  for (let y = 40; y < height; y += 40) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
  }

  const points = [];
  (strokes || []).forEach(stroke => (stroke || []).forEach(point => points.push(point)));
  if (!points.length) {
    ctx.fillStyle = '#738197';
    ctx.font = '14px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText('No stroke data stored', width / 2, height / 2);
    return;
  }

  const xs = points.map(point => point[0]);
  const ys = points.map(point => point[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const boundsWidth = Math.max(maxX - minX, 1);
  const boundsHeight = Math.max(maxY - minY, 1);
  const margin = 22;
  const scale = Math.min((width - 2 * margin) / boundsWidth, (height - 2 * margin) / boundsHeight);
  const offsetX = (width - boundsWidth * scale) / 2;
  const offsetY = (height - boundsHeight * scale) / 2;
  const mapPoint = point => [
    offsetX + (point[0] - minX) * scale,
    offsetY + (point[1] - minY) * scale
  ];

  ctx.strokeStyle = '#172033';
  ctx.lineWidth = Math.max(3, Math.min(width, height) / 90);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  (strokes || []).forEach(stroke => {
    if (!stroke || stroke.length < 2) return;
    const first = mapPoint(stroke[0]);
    ctx.beginPath();
    ctx.moveTo(first[0], first[1]);
    for (let i = 1; i < stroke.length; i++) {
      const point = mapPoint(stroke[i]);
      ctx.lineTo(point[0], point[1]);
    }
    ctx.stroke();
  });
}

function getAttempts(row) {
  const attempts = jsonValue(row.attempts, []);
  return Array.isArray(attempts) ? attempts : [];
}

function renderEnrollmentList() {
  const query = $('search').value.trim().toLowerCase();
  const box = $('enrollmentList');
  box.innerHTML = '';
  const filtered = enrollments.filter(row =>
    JSON.stringify({
      id: row.id,
      participant_id: row.participant_id,
      seed_label: row.seed_label
    }).toLowerCase().includes(query)
  );

  if (!filtered.length) {
    box.innerHTML = '<div class="empty-state">No enrollments match this search.</div>';
    return;
  }

  filtered.forEach(row => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'rowitem' + (selected && selected.id === row.id ? ' active' : '');

    const title = document.createElement('strong');
    title.textContent = row.seed_label || 'drawing_seed';
    const meta = document.createElement('span');
    const ar = row.analysis_result || {};
    const q = ar.seed_quality_score || (ar.seed_quality && ar.seed_quality.quality_score);
    meta.textContent = `${row.participant_id || 'no participant'} · ${row.recommended_profile || 'no profile'} · stability ${n3(row.stability_score)}` + (q !== undefined ? ` · quality ${n3(q)}/100` : '');
    const date = document.createElement('span');
    date.className = 'tiny';
    date.textContent = `${row.created_at || ''} · ${shortId(row.id)}`;

    button.append(title, meta, date);
    button.onclick = () => selectEnrollment(row.id);
    box.appendChild(button);
  });
}

function renderSelected() {
  if (!selected) return;
  $('selectedTitle').textContent = selected.seed_label || 'drawing_seed';
  const ar = selected.analysis_result || {};
  const q = ar.seed_quality_score || (ar.seed_quality && ar.seed_quality.quality_score);
  const ql = ar.seed_quality_label || (ar.seed_quality && ar.seed_quality.quality_label);
  $('selectedMeta').textContent = `${selected.participant_id || 'no participant'} · ${selected.recommended_profile || 'no profile'} · stability ${n3(selected.stability_score)}` + (q !== undefined ? ` · quality ${n3(q)}/100 ${ql || ''}` : '') + ` · ID ${selected.id}`;
  $('selectedStatus').textContent = selected.accepted_for_demo ? 'Accepted enrollment' : 'Weak enrollment';
  $('selectedStatus').className = 'status-pill ' + (selected.accepted_for_demo ? 'granted' : 'denied');

  const previews = $('enrollmentPreviews');
  previews.innerHTML = '';
  const attempts = getAttempts(selected);
  attempts.forEach((attempt, index) => {
    const card = document.createElement('div');
    card.className = 'preview-card';
    const label = document.createElement('div');
    label.className = 'tiny';
    label.textContent = `Enrollment attempt ${index + 1}`;
    const preview = document.createElement('canvas');
    preview.width = 360;
    preview.height = 220;
    card.append(label, preview);
    previews.appendChild(card);
    drawPreview(preview, attempt.strokes || []);
  });
}

function scoreBlock(label, value) {
  const block = document.createElement('div');
  block.className = 'metric';
  const caption = document.createElement('span');
  caption.textContent = label;
  const score = document.createElement('strong');
  score.textContent = n3(value);
  block.append(caption, score);
  return block;
}

function showVerificationDetail(row) {
  selectedVerification = row;
  $('verificationDetail').classList.remove('hidden');
  $('detailTitle').textContent = attemptLabel(row);
  $('detailMeta').textContent = `${row.attempt_type || 'unknown type'} · ${row.created_at || ''} · ID ${row.id}`;
  $('detailStatus').textContent = row.accepted ? 'Accepted' : 'Rejected';
  $('detailStatus').className = 'status-pill ' + (row.accepted ? 'granted' : 'denied');
  $('detailAttemptType').value = row.attempt_type || 'owner_test';
  drawPreview($('detailCanvas'), row.redraw_strokes || []);

  const result = jsonValue(row.verification_result, {}) || {};
  const geometry = result.geometry_scores || {};
  const metrics = $('detailMetrics');
  metrics.innerHTML = '';
  [
    ['Final score', row.final_score],
    ['Token score', row.token_score],
    ['Geometry', row.geometry_final],
    ['Layout', row.layout_score ?? geometry.layout],
    ['Relation', row.relation_score ?? geometry.relation],
    ['Curve', row.curve_score ?? geometry.curve],
    ['Stroke shape', row.stroke_shape_score ?? geometry.stroke_shape],
    ['Fuzzy', row.fuzzy_ok ? 1 : 0]
  ].forEach(([label, value]) => metrics.appendChild(scoreBlock(label, value)));

  $('detailJson').textContent = JSON.stringify(row, null, 2);
  $('verificationDetail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderVerifications() {
  const box = $('verificationList');
  box.innerHTML = '';
  if (!selected) {
    box.innerHTML = '<div class="empty-state">Select an enrollment to view its verification attempts.</div>';
    return;
  }
  if (!selectedVerifications.length) {
    box.innerHTML = '<div class="empty-state">No verification attempts are stored for this enrollment.</div>';
    return;
  }

  selectedVerifications.forEach(row => {
    const item = document.createElement('article');
    item.className = 'verification-row';

    const identity = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = attemptLabel(row);
    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = `${row.attempt_type || 'unknown type'} · ${row.created_at || ''}`;
    identity.append(title, meta);

    const status = document.createElement('div');
    status.innerHTML = `<span class="status-pill ${row.accepted ? 'granted' : 'denied'}">${row.accepted ? 'Accepted' : 'Rejected'}</span>`;

    const score = document.createElement('div');
    score.className = 'score';
    score.textContent = `final ${n3(row.final_score)}\ntoken ${n3(row.token_score)}`;

    const actions = document.createElement('div');
    actions.className = 'verification-actions';
    const view = document.createElement('button');
    view.type = 'button';
    view.className = 'btn small-btn';
    view.textContent = 'View';
    view.onclick = () => showVerificationDetail(row);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn red small-btn';
    remove.textContent = 'Delete';
    remove.onclick = () => deleteVerification(row);
    actions.append(view, remove);

    item.append(identity, status, score, actions);
    box.appendChild(item);
  });
}

async function refreshAll() {
  $('enrollmentList').innerHTML = '<div class="empty-state">Loading enrollments...</div>';
  const data = await getJson('/api/dev/enrollments');
  enrollments = data.enrollments || [];
  renderEnrollmentList();
}

async function selectEnrollment(id) {
  const data = await getJson('/api/dev/enrollments/' + id);
  selected = data.enrollment;
  selectedVerifications = data.verifications || [];
  selectedVerification = null;
  $('verificationDetail').classList.add('hidden');
  renderEnrollmentList();
  renderSelected();
  renderVerifications();
  drawing.clear();
  $('verifyOutput').textContent = 'Enrollment selected. Draw and log a new verification attempt.';
  $('verifyOutput').className = 'output';
}

function renderVerifyResult(result) {
  const geometry = result.geometry_scores || {};
  const lines = [
    result.accepted ? 'ACCESS GRANTED' : 'ACCESS DENIED',
    'Verification ID: ' + (result.verification_id || 'not logged'),
    'Final score: ' + n3(result.final_score),
    'Token score: ' + n3(result.token_score),
    'Geometry final: ' + n3(geometry.geometry_final),
    'Layout: ' + n3(geometry.layout),
    'Relation: ' + n3(geometry.relation),
    'Curve: ' + n3(geometry.curve),
    'Shape: ' + n3(geometry.stroke_shape),
    'Fuzzy: ' + (result.fuzzy_recovery ? (result.fuzzy_recovery.ok ? 'OK' : 'FAILED') : 'not run')
  ];
  if (result.high_confidence_override) {
    lines.splice(3, 0, 'Decision: accepted by high-confidence override (> 0.800)');
  }
  if (geometry.topology !== undefined) lines.splice(7, 0, 'Topology: ' + n3(geometry.topology));
  if (geometry.topology_flags && geometry.topology_flags.length) {
    lines.push('Topology notes: ' + geometry.topology_flags.join(', '));
  }
  if (geometry.closed_style_applicable === false) lines.push('Closed style: not applicable');
  else if (geometry.closed_style !== undefined) lines.push('Closed style: ' + n3(geometry.closed_style));
  if (result.failure_reasons && result.failure_reasons.length) {
    lines.push('Failure reasons: ' + result.failure_reasons.join(', '));
  }
  if (result.step_up_challenge) {
    lines.push('Step-up challenge: ' + result.step_up_challenge.prompt);
  }
  $('verifyOutput').textContent = lines.join('\n');
  $('verifyOutput').className = 'output ' + (result.accepted ? 'ok' : 'bad');
}

async function deleteVerification(row) {
  if (!confirm(`Delete “${attemptLabel(row)}” from Supabase?`)) return;
  await deleteJson('/api/dev/verifications/' + row.id);
  if (selectedVerification && selectedVerification.id === row.id) {
    selectedVerification = null;
    $('verificationDetail').classList.add('hidden');
  }
  await selectEnrollment(selected.id);
}

$('refreshAll').onclick = refreshAll;
$('search').oninput = renderEnrollmentList;
$('undo').onclick = () => drawing.undo();
$('clear').onclick = () => drawing.clear();

$('submitVerification').onclick = async () => {
  const button = $('submitVerification');
  try {
    if (!selected) {
      $('verifyOutput').textContent = 'Select an enrollment first.';
      return;
    }
    const strokes = drawing.get();
    if (!strokes.length) {
      $('verifyOutput').textContent = 'Draw a verification attempt first.';
      return;
    }
    button.disabled = true;
    button.textContent = 'Verifying...';
    $('verifyOutput').textContent = 'Verifying and logging the attempt...';

    const result = await postJson('/api/dev/verify_existing', {
      enrollment_id: selected.id,
      redraw_strokes: strokes,
      attempt_type: $('attemptType').value,
      participant_id: $('testerId').value || 'dev_tester',
      seed_label: selected.seed_label || 'drawing_seed',
      ui_version: 'dev-existing-enrollment-verifier'
    });

    await selectEnrollment(selected.id);
    renderVerifyResult(result);
    if (result.verification_id) {
      const saved = selectedVerifications.find(row => row.id === result.verification_id);
      if (saved) showVerificationDetail(saved);
    }
  } catch (error) {
    $('verifyOutput').textContent = error.message;
    $('verifyOutput').className = 'output bad';
  } finally {
    button.disabled = false;
    button.textContent = 'Verify and log';
  }
};

$('updateAttemptType').onclick = async () => {
  if (!selectedVerification) return;
  const button = $('updateAttemptType');
  const attemptType = $('detailAttemptType').value;
  try {
    button.disabled = true;
    button.textContent = 'Saving...';
    const data = await patchJson('/api/dev/verifications/' + selectedVerification.id, {
      attempt_type: attemptType
    });
    const updated = data.verification;
    selectedVerifications = selectedVerifications.map(row => row.id === updated.id ? updated : row);
    selectedVerification = updated;
    renderVerifications();
    showVerificationDetail(updated);
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Update type';
  }
};

$('deleteVerification').onclick = () => {
  if (selectedVerification) deleteVerification(selectedVerification);
};

$('deleteEnrollment').onclick = async () => {
  if (!selected) {
    alert('Select an enrollment first.');
    return;
  }
  if (!confirm(`Delete enrollment “${selected.seed_label || shortId(selected.id)}” and every linked verification attempt?`)) return;
  if (!confirm('This cannot be undone. Delete it permanently?')) return;
  await deleteJson('/api/dev/enrollments/' + selected.id);
  selected = null;
  selectedVerifications = [];
  selectedVerification = null;
  $('selectedTitle').textContent = 'Choose an enrollment';
  $('selectedMeta').textContent = 'The record was deleted. Select another enrollment.';
  $('selectedStatus').textContent = 'Idle';
  $('selectedStatus').className = 'status-pill neutral';
  $('enrollmentPreviews').innerHTML = '';
  $('verificationDetail').classList.add('hidden');
  renderVerifications();
  await refreshAll();
};

refreshAll().catch(error => {
  $('enrollmentList').innerHTML = '';
  const state = document.createElement('div');
  state.className = 'empty-state';
  state.textContent = error.message;
  $('enrollmentList').appendChild(state);
});
