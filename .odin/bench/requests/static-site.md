I want a static site generator for my markdown blog. Posts are markdown files
with front matter (title, date, tags) in a posts/ folder.
It should produce plain HTML files and an RSS feed (rss.xml) listing the latest 20 posts.
Deployment is just copying the output folder to a web server, so the output must
be plain files with no server-side parts.
No JavaScript framework on the generated pages, plain HTML and a little CSS only.
Write the generator itself in whatever language you think is simplest,
but one command should rebuild everything.
Tag pages would be nice.
