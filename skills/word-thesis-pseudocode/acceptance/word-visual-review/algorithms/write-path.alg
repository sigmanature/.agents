@algorithm id=write-path chapter=auto index=auto title="Write Path"

Input: inode, pos
Output: status
for each bio in batch:
    submit bio
return success