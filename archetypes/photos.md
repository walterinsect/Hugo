+++
date = '{{ .Date }}'
draft = true
title = '{{ replace .File.ContentBaseName "-" " " | title }}'
# Flickr のアルバムページの URL（「Flickr で見る」のリンク先）
flickr_album = ''
# 表紙にする写真の URL（省略すると 1 枚目）
# cover = ''

# 写真ごとに Flickr の写真ページの URL を書く
[[photos]]
  url = ''
  # caption = ''
+++
