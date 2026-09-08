import React from 'react'
import mark from '../assets/rongda-ai-mark.svg'

/** The standalone mark stays legible in small avatars and collapsed navigation. */
const BrandMark: React.FC<{ className?: string; decorative?: boolean }> = ({
  className = '',
  decorative = false,
}) => (
  <img
    src={mark}
    alt={decorative ? '' : '容大AI'}
    draggable={false}
    className={`object-contain flex-shrink-0 ${className}`}
  />
)

export default BrandMark
