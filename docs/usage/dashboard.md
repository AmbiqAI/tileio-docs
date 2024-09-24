# :material-widgets: Dashboard

## Overview

A dashboard consists of the following components:

* **Info**: The dashboard name and description.
* **Slots**: A set of slots that define the data streams from a device.
* **I/O**: A set of user input/output elements that can be used to interact with the device.
* **Tiles**: A set of tiles that display data from a device.

## :material-widgets: Info

The dashboard name and description can be configured by clicking on the dashboard button in the navigation bar and selecting the `Info` tab. The dashboard name and description will be displayed at the top of the dashboard. To edit the dashboard name and description, click on the `pencil` icon. To enable styling, the description accepts [markdown](https://www.markdownguide.org/basic-syntax/).

<figure markdown="span">
  ![Tielio Info Setup](/assets/tileio-imu-info.png){ width="768" }
  <figcaption></figcaption>
</figure>

## :fontawesome-solid-chart-bar: Slots

In Tileio, a device streams data over a predefined set of **slots**. A **slot** consists of a set of similar signals (e.g. accelerometer) captured at the same sampling rate (e.g. 100 Hz). More specifically, a **slot** consists of two components: (1) a set of **channels** (e.g. X, Y, Z) along with a **mask** and (2) a set of **metrics**. A device can have up to 4 slots with each slot having up to 4 channels and up to 60 metrics. In total, a device can stream up to 16 channels and 240 metrics.

In order for Tileio to know how to interpret the data being streamed from a device, the slots must be configured. The configuration of a slot consists of the following:

* **enabled**: A flag to indicate if the slot is enabled.
* **name**: The name of the slot (e.g. IMU).
* **units**: The units of the data being streamed (e.g. m/s^2).
* **fs**: The sampling rate of the data being streamed (e.g. 100 Hz). This is necessary to correctly estimate the time axis on the dashboard.
* **dtype**: The data type of the data being streamed (e.g. float32, int16).
* **chs**: A list of channel names (e.g. X, Y, Z).
* **metrics**: A list of metrics broadcasted by the device (e.g. e.g. fall).

The slots can be configured in the dashboard by clicking on the dashboard button in the navigation bar and selecting the `Slots` tab. The slots will be displayed as a list of cards with the above information displayed. To edit a slot, click on the `pencil` icon for the desired slot.

<figure markdown="span">
  ![Tielio Slot Setup](/assets/tileio-slot-setup.png){ width="768" }
  <figcaption></figcaption>
</figure>

## :material-tune: I/O

Beyond streaming data, Tileio also provides a generic I/O (input/output) control interface that can be used to interact with the device. The interface consists of 8 configurable I/O elements that be configured as buttons, toggle switches, sliders, and select boxes.

In order for Tileio to know how to interpret the I/O data being transferred to the device, the I/O elements must be configured. The configuration of an I/O element consists of the following:

* **enabled**: A flag to indicate if the I/O element is enabled.
* **name**: The name of the I/O element (e.g. Button).
* **direction**: The direction of the I/O element: `input` or `output`. If `input`, the I/O is controlled by the user.
* **type**: The type of the I/O element: `Momentary`, `Toggle`, `Slider`, `Select`.
* **type-specific**: Additional configuration parameters based on the type of the I/O element.

The I/O elements can be configured in the dashboard by clicking on the dashboard button in the navigation bar and selecting the `I/O` tab. The I/O elements will be displayed as a list of cards with the above information displayed. To edit an I/O element, click on the `pencil` icon for the desired I/O element.

<figure markdown="span">
  ![Tielio IO Setup](/assets/tileio-io-setup.png){ width="768" }
  <figcaption></figcaption>
</figure>

## :material-view-dashboard: Tiles

A dashboard consists of a collection of tiles that display data from a device. Each tile can be configured to display data from a specific slot and channel. Tileio supports a number of built-in tile types that have their own configuration parameters. Each tile consists of the following:

* **name**: The user-defined name of the tile.
* **type**: The type of the tile (e.g. Signal Stream Plot)
* **size**: Size of the tile (e.g. 'small', 'medium', 'large').
* **config**: Configuration parameters specific to the tile type.

The tiles can be configured in the dashboard by clicking on the dashboard button in the navigation bar and selecting the `Tiles` tab. The tiles will be displayed as a list of cards with the above information displayed along with buttons to move the tile, change the size, delete the tile, and edit the tile configuration. To edit a tile, click on the `pencil` icon for the desired tile. This will display the tile's custom parameter form. Please refer to [Add Tile Guide](../guides/new-tile.md) for more information on how to add a new tile to the dashboard.

<figure markdown="span">
  ![Tielio Tiles Setup](/assets/tileio-tile-setup.png){ width="768" }
  <figcaption></figcaption>
</figure>
