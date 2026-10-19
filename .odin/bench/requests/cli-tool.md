I need a command line tool in Python that finds duplicate photos in a folder.
It should use perceptual hashing (pHash) rather than exact file hashes, so
resized or re-compressed copies are caught too.
The report must come out as JSON on stdout, grouped by duplicate set, so I can
pipe it into jq.
No third-party services or uploads at all, everything has to run offline on my
laptop. It will mostly be run on folders with a few thousand JPEGs and PNGs.
I don't want it to delete anything, just report.
