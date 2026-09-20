// JPEG 压缩：长边缩到 ≤ maxEdge，按质量阶梯压到 ≤ maxBytes（§6）。
// 返回 {dataUrl, width, height, bytes}。

export async function compressJpeg(
  sourceDataUrl,
  { maxEdge = 1280, maxBytes = 2 * 1024 * 1024, startQuality = 0.85, minQuality = 0.5 } = {}
) {
  const img = await loadImage(sourceDataUrl);
  const scale = Math.min(1, maxEdge / Math.max(img.naturalWidth, img.naturalHeight));
  const width = Math.round(img.naturalWidth * scale);
  const height = Math.round(img.naturalHeight * scale);

  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0, width, height);

  let quality = startQuality;
  let blob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
  while (blob.size > maxBytes && quality > minQuality) {
    quality -= 0.1;
    blob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
  }
  if (blob.size > maxBytes) {
    // 极端情况下缩小尺寸再压一轮。
    return compressJpeg(sourceDataUrl, {
      maxEdge: Math.round(maxEdge * 0.8),
      maxBytes,
      startQuality,
      minQuality,
    });
  }
  const dataUrl = await blobToDataUrl(blob);
  return { dataUrl, width, height, bytes: blob.size };
}

function mapCropToImage(crop, imageWidth, imageHeight) {
  const vw = Number(crop && crop.viewportWidth) || 1;
  const vh = Number(crop && crop.viewportHeight) || 1;
  const imgW = Math.max(1, Math.round(Number(imageWidth) || 0));
  const imgH = Math.max(1, Math.round(Number(imageHeight) || 0));
  let sx = Math.round((Number(crop && crop.left) || 0) * imgW / vw);
  let sy = Math.round((Number(crop && crop.top) || 0) * imgH / vh);
  let sw = Math.round((Number(crop && crop.width) || 0) * imgW / vw);
  let sh = Math.round((Number(crop && crop.height) || 0) * imgH / vh);
  sx = Math.min(Math.max(0, sx), imgW - 1);
  sy = Math.min(Math.max(0, sy), imgH - 1);
  sw = Math.max(1, Math.min(sw, imgW - sx));
  sh = Math.max(1, Math.min(sh, imgH - sy));
  return { sx, sy, sw, sh };
}

export async function cropJpeg(sourceDataUrl, crop) {
  const img = await loadImage(sourceDataUrl);
  const box = mapCropToImage(crop, img.naturalWidth, img.naturalHeight);
  if (box.sw < 4 || box.sh < 4) {
    const error = new Error('格子裁切区域过小');
    error.code = 'CAPTURE_FAILED';
    throw error;
  }
  const canvas = new OffscreenCanvas(box.sw, box.sh);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, box.sx, box.sy, box.sw, box.sh, 0, 0, box.sw, box.sh);
  const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: 0.92 });
  const dataUrl = await blobToDataUrl(blob);
  return { dataUrl, width: box.sw, height: box.sh, bytes: blob.size };
}

function loadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = dataUrl;
  });
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
