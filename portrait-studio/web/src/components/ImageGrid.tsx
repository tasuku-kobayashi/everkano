import { useMemo } from "react";
import { FixedSizeGrid } from "react-window";
import type { ImageItem } from "../api/client";
import { useElementSize } from "../hooks/useElementSize";
import { ImageCard } from "./ImageCard";

interface Props {
  images: ImageItem[];
  onOpen: (index: number) => void;
  onFavorite?: (image: ImageItem) => void;
  onRegenerate?: (image: ImageItem) => void;
  onSimilarity?: (image: ImageItem) => void;
  selectedIds?: Set<string>;
  onSelect?: (image: ImageItem) => void;
  showCharacter?: boolean;
  minColumnWidth?: number;
  height?: number;
}

const CARD_ASPECT = 1216 / 832;
const CARD_EXTRA = 44; // caption rows

/**
 * Virtualized grid (react-window). 500+ images stay smooth because only visible cards are mounted,
 * and every card uses the thumbnail endpoint.
 */
export function ImageGrid({ images, onOpen, onFavorite, onRegenerate, onSimilarity, selectedIds, onSelect, showCharacter, minColumnWidth = 200, height }: Props) {
  const [ref, size] = useElementSize<HTMLDivElement>();
  const width = size.width || 800;
  const gridHeight = height ?? Math.max(300, size.height || 600);
  const columnCount = Math.max(1, Math.floor(width / minColumnWidth));
  const columnWidth = Math.floor(width / columnCount);
  const rowHeight = Math.round((columnWidth - 8) * CARD_ASPECT) + CARD_EXTRA;
  const rowCount = Math.ceil(images.length / columnCount);
  const itemData = useMemo(() => ({ images, columnCount }), [images, columnCount]);

  return (
    <div ref={ref} className="h-full w-full" data-testid="image-grid" data-count={images.length}>
      {images.length === 0 ? (
        <div className="p-8 text-center text-sm text-slate-500">画像がありません</div>
      ) : (
        <FixedSizeGrid
          columnCount={columnCount}
          columnWidth={columnWidth}
          height={gridHeight}
          rowCount={rowCount}
          rowHeight={rowHeight}
          width={width}
          itemData={itemData}
          overscanRowCount={2}
        >
          {({ columnIndex, rowIndex, style, data }) => {
            const index = rowIndex * data.columnCount + columnIndex;
            const image = data.images[index];
            if (!image) return null;
            return (
              <div style={style} className="p-1">
                <ImageCard
                  image={image}
                  onOpen={() => onOpen(index)}
                  onFavorite={onFavorite ? () => onFavorite(image) : undefined}
                  onRegenerate={onRegenerate ? () => onRegenerate(image) : undefined}
                  onSimilarity={onSimilarity ? () => onSimilarity(image) : undefined}
                  selected={selectedIds?.has(image.id)}
                  onSelect={onSelect ? () => onSelect(image) : undefined}
                  showCharacter={showCharacter}
                />
              </div>
            );
          }}
        </FixedSizeGrid>
      )}
    </div>
  );
}
