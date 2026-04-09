This app requires flask and pillow be installed with pip or with "python -m pip install flask pillow" from command prompt, this app was written in python version 3.11.7.
I recommend installing this in C:\Python\CloneHeroCatalogue for best results.

This is a simple Flask web server that scans your Clone Hero song directory and populates a nice catalogue with A-Z jump list,
arrow key scrolling (for older touch devices that do not support drag to scroll), sort by Artist or Song, in Artist view it will
let you click each artist to view what songs per artist, in Title view it lets you see all the songs in alphabetical order.
Also has a "Request" function so you can add song requests as either a reminder for yourself or as a way for friends to request
songs when they come play. It populates a json file in the root directory of the script.

On first run it will create all the default directories and files in the scripts root folder. It will then prompt you to select
the Clone Hero song folder. It will then scan for all the songs while also converting any "album.jpg" or "album.jpeg" files to "album.png"
while leaving the original intact, this is for he Flask server to generate the album covers in the web interfafce. Once that is done, it
starts a web server on port 80 of the devices local IP address.

Settings in the config.json include changing the port number, the songs directory path, and you can also add folder exclusions by adding 


"exclude_paths": [
    "Guitar Hero/Guitar Hero II"
    ]


To the config.json file.
