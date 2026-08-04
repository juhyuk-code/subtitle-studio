export interface MediaBounds {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function subtitlePreviewScale(
  displayedVideoHeight: number,
  subtitleCanvasHeight = 1080
): number {
  if (displayedVideoHeight <= 0 || subtitleCanvasHeight <= 0) return 0;
  return displayedVideoHeight / subtitleCanvasHeight;
}

export function containedMediaBounds(
  containerWidth: number,
  containerHeight: number,
  mediaWidth: number,
  mediaHeight: number
): MediaBounds {
  if (
    containerWidth <= 0 ||
    containerHeight <= 0 ||
    mediaWidth <= 0 ||
    mediaHeight <= 0
  ) {
    return {
      left: 0,
      top: 0,
      width: Math.max(0, containerWidth),
      height: Math.max(0, containerHeight)
    };
  }
  const mediaRatio = mediaWidth / mediaHeight;
  const containerRatio = containerWidth / containerHeight;
  const width =
    mediaRatio > containerRatio
      ? containerWidth
      : containerHeight * mediaRatio;
  const height =
    mediaRatio > containerRatio
      ? containerWidth / mediaRatio
      : containerHeight;
  return {
    left: (containerWidth - width) / 2,
    top: (containerHeight - height) / 2,
    width,
    height
  };
}
