# :material-view-dashboard: Tiles

A dashboard consists of a collection of tiles that display data from a device. Each tile can be configured to display data from a specific slot and channel. Tileio supports a number of built-in tile types that have their own configuration parameters. Each tile consists of the following:

* **name**: The user-defined name of the tile.
* **type**: The type of the tile (e.g. Signal Stream Plot)
* **size**: Size of the tile (e.g. 'small', 'medium', 'large').
* **config**: Configuration parameters specific to the tile type.

The tiles can be configured in the dashboard by clicking on the dashboard button in the navigation bar and selecting the `Tiles` tab. The tiles will be displayed as a list of cards with the above information displayed along with buttons to move the tile, change the size, delete the tile, and edit the tile configuration. To edit a tile, click on the `pencil` icon for the desired tile. This will display the tile's custom parameter form. Please refer to [Add Tile Guide](../guides/new-tile.md) for more information on how to add a new tile to the dashboard.

## Available Tiles

* **QoS Tile**: A tile that displays the quality of service (QoS) of the data stream.
* **Markdown Tile**: A tile that displays markdown text.
* **Event Tile**: Enables the user to trigger an event at a specific time for specific slot.
* **Sparkline Tile**: Display a sparkline plot for specific slot metric.
* **Bar Slide Tile**: Display a set of slides containing static bar charts or big numbers.
* **Signal Stream Plot**: Display a real-time plot of a signal stream for specified channels.
* **Metrics Stream Plot**: Display a real-time plot of a metric stream for specified metrics.
* **Segment Stream Plot**: Display a real-time plot highlighting segments of a signal stream.
* **Seg Pie Tile**: Display a pie chart of the segments of a signal stream.
* **Fiducial Pie Tile**: Display a pie chart of the fiducial markers of a signal stream.
* **UIO Tile**: Display I/O (input/ouptut) controls for the device.
* **IO Tile**: Display a signle I/O element for the device.
* **SVG Tile**: Display an SVG image.
* **Metric Text**: Display corresponding text/label for given metric value.
