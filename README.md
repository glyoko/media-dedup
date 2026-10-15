# media-dedup

A command line tool for deduplicating media files.

## What it is

Say you've got a memes folder with 1000 images in it. You downloaded them from disparate sources. Maybe the web, maybe Discord, maybe a friend texted them to you. Maybe some of them have been shared multiple times, and their quality has degraded. Maybe their dimensions were changed, and changed again. Maybe some are watermarked. What this tool does is it finds media that is "similar", and cleans up your folders.

If it's not sure, it'll pause to ask you what to do. If it's positive that two files are the same, it'll remove one of them. If two images are basically the same, but one is of higher quality, it'll keep the higher quality image.

It works for both images and videos.

## options

`-d`/`--dry-run` | Output what files are duplicates and which would be removed, but don't actually remove anything.
