// Phone photos are 3 to 8 MB. Receipt text stays legible at 2000 px on the long edge, and the
// upload (and what the model is sent) drops to a few hundred KB. Anything the browser can't
// decode goes up as it is and the server says whether it can use it.
export async function shrinkPhoto(file, max = 2000) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' })
    const scale = Math.min(1, max / Math.max(bmp.width, bmp.height))
    if (scale === 1 && file.type === 'image/jpeg' && file.size < 1_500_000) return file
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bmp.width * scale)
    canvas.height = Math.round(bmp.height * scale)
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height)
    bmp.close?.()
    return await new Promise((ok) => canvas.toBlob((b) => ok(b || file), 'image/jpeg', 0.86))
  } catch {
    return file
  }
}
