import { $, esc, clock } from './common.js';
import { viewFrames } from './frames.js';
import { frameAt, bounds } from './playback.js';
import { animate } from './motion.js';

export function renderViewer(s, ui) {
  const available = viewFrames(s);
  const cameras = [...new Set(available.map(f => f.camera))];
  if (!cameras.includes(ui.camera)) ui.camera = cameras.includes('head') ? 'head' : cameras[0] || '';
  const cameraMarkup = cameras.map(c => {
    const count = available.filter(f => f.camera === c).length;
    return `<option value="${esc(c)}">${esc(c.replaceAll('_', ' '))} · ${count} frames</option>`;
  }).join('') || '<option>No camera</option>';
  if ($('camera').innerHTML !== cameraMarkup) $('camera').innerHTML = cameraMarkup;
  $('camera').value = ui.camera;
  const range = bounds(s);
  if (ui.follow) ui.time = range.end;
  const time = ui.time ?? range.start;
  const frames = available.filter(f => f.camera === ui.camera);
  const frame = frameAt(available, ui.camera, time);
  ui.frameId = frame?.id;
  const carried = !!frame && time - frame.timestamp > .5;
  const slider = $('frame-slider');
  slider.max = Math.max(0, range.end - range.start);
  slider.value = Math.max(0, time - range.start);
  slider.disabled = !available.length;
  slider.setAttribute('aria-valuetext', `${clock(time - range.start)} of ${clock(range.end - range.start)}`);
  $('playback-time').textContent = `${clock(time - range.start)} / ${clock(range.end - range.start)}`;
  $('frame-count').textContent = frame ? `${frames.indexOf(frame) + 1} / ${frames.length}` : `${frames.length} recorded`;
  $('frame').hidden = !frame;
  $('frame-empty').hidden = !!frame;
  $('frame-badge').hidden = !frame;
  if (frame) {
    showFrame(frame.url, !ui.playing || displayedCamera !== ui.camera);
    displayedCamera = ui.camera;
    $('frame').alt = `${frame.camera} observation at ${new Date(frame.timestamp * 1000).toLocaleTimeString()}`;
    $('frame-badge').textContent = carried ? '↳ Holding previous frame' : frame.batch_id ? 'Linked tool output' : frame.association;
    $('frame-badge').classList.toggle('carried', carried);
    $('frame-info').textContent = `${new Date(frame.timestamp * 1000).toLocaleTimeString()}${frame.state_version != null ? ` · state ${frame.state_version}` : ''}${carried ? ` · held ${(time - frame.timestamp).toFixed(1)}s` : ''}`;
  } else {
    requested = '';
    $('frame-previous').hidden = true;
    $('frame-empty').querySelector('strong').textContent = 'No earlier observation';
    $('frame-empty').querySelector('span:last-child').textContent = 'This camera has not recorded a frame at the selected time.';
    $('frame-info').textContent = 'Waiting for the first recorded frame';
  }
  renderCameras(available, cameras, ui, time);
  renderRail(s, available, range, time);
  const batchIndex = s.batches.findIndex(b => b.id === ui.batchId);
  $('mode-label').textContent = ui.follow ? 'FOLLOWING LATEST' : `${ui.playing ? 'PLAYING' : 'PAUSED'}${batchIndex >= 0 ? ` · BATCH ${String(batchIndex + 1).padStart(2, '0')} / ${s.total_batches}` : ''}`;
  $('follow').textContent = ui.follow ? '● Following latest' : '↗ Return to latest';
  $('follow').classList.toggle('active', ui.follow);
  $('play').textContent = ui.playing ? 'Ⅱ' : '▶';
  $('play').setAttribute('aria-label', ui.playing ? 'Pause playback' : 'Play timeline');
}

function renderCameras(frames, cameras, ui, time) {
  const names = cameras.filter(c => c !== ui.camera && c !== 'locate_overlay');
  const node = $('camera-tiles');
  const signature = names.join('|');
  if (node.dataset.cameras !== signature) {
    node.dataset.cameras = signature;
    node.innerHTML = names.map(c => `<button class="camera-tile" data-camera="${esc(c)}" aria-label="Make ${esc(c.replaceAll('_', ' '))} main view"><div class="camera-preview"><img alt="${esc(c)} synchronized view" hidden><span class="camera-unavailable">No earlier frame</span></div><div class="camera-caption"><span>${esc(c.replaceAll('_', ' '))}</span><span class="camera-state"></span><span class="promote">↗</span></div></button>`).join('');
  }
  node.hidden = !names.length;
  [...node.children].forEach((tile, i) => {
    const frame = frameAt(frames, names[i], time);
    const image = tile.querySelector('img');
    tile.querySelector('.camera-unavailable').hidden = !!frame;
    image.hidden = !frame;
    if (frame) {
      const url = frame.url + '?width=320';
      if (image.getAttribute('src') !== url) image.src = url;
      image.dataset.timestamp = frame.timestamp;
      tile.querySelector('.camera-state').textContent = `${frame.state_version != null ? `s${frame.state_version}` : ''}${time - frame.timestamp > .5 ? ' · held' : ''}`;
    } else tile.querySelector('.camera-state').textContent = '—';
  });
}

let railState = null;
function renderRail(s, frames, range, time) {
  const duration = Math.max(.001, range.end - range.start);
  const percent = t => Math.max(0, Math.min(100, (t - range.start) / duration * 100));
  if (railState !== s) {
    railState = s;
    const ticks = [...new Set(frames.map(f => f.timestamp).filter(Number.isFinite))];
    $('frame-ticks').setAttribute('d', ticks.map(t => `M${percent(t) * 10},0v8`).join(''));
    $('time-segments').innerHTML = s.batches.filter(b => Number.isFinite(b.timestamp)).map((b, i, all) => {
      const start = percent(b.timestamp), end = percent(all[i + 1]?.timestamp ?? range.end);
      return `<span style="left:${start}%;width:${end - start}%" title="Batch ${i + 1}"></span>`;
    }).join('');
  }
  const progress = percent(time);
  $('playback-rail').style.setProperty('--progress', `${progress}%`);
}

let requested = '', displayedCamera = '';
function showFrame(url, transitionEnabled) {
  url += '?width=960';
  const image = $('frame'), previous = $('frame-previous');
  if (requested === url) return;
  requested = url;
  const loader = new Image();
  loader.onload = () => {
    if (requested !== url) return;
    const old = image.getAttribute('src');
    if (transitionEnabled && old && old !== url && image.naturalWidth) {
      previous.src = old;
      previous.hidden = false;
    } else previous.hidden = true;
    image.src = url;
    const transition = !previous.hidden && animate(previous, [{opacity:1}, {opacity:0}], 180);
    const done = () => { if (requested === url) previous.hidden = true; };
    if (transition) transition.finished.then(done, () => {}); else done();
  };
  loader.onerror = () => {
    if (requested !== url) return;
    requested = '';
    previous.hidden = true;
    $('frame-badge').textContent = 'Image unavailable · last loaded view retained';
  };
  loader.src = url;
}

export function viewerReady() {
  return (!requested || $('frame').getAttribute('src') === requested)
    && [...$('camera-tiles').querySelectorAll('img:not([hidden])')].every(image => image.complete);
}
